import os
import sys
import hashlib
import configparser
import shutil
from pathlib import Path

# --- KONFIGURACJA ---
CONFIG_FILE = Path.home() / ".clean_files"

def load_config():
    """Wczytuje parametry z pliku konfiguracyjnego."""
    config = configparser.ConfigParser()
    
    # Ustawienie wartości domyślnych na wypadek braku pliku konfiguracyjnego
    config['Settings'] = {
        'suggested_permissions': 'rw-r--r--',
        'troublesome_chars': ':;*?"$#`|\\.', # Znak \ wymaga podwójnego zescrapowania w stringu
        'char_substitute': '_',
        'temp_extensions': '.tmp,~,.bak,.DS_Store',
    }
    
    if not CONFIG_FILE.exists():
        print(f"⚠️ Uwaga: Nie znaleziono pliku konfiguracyjnego: {CONFIG_FILE}")
        # Można zapisać domyślny plik, by ułatwić edycję
        with open(CONFIG_FILE, 'w') as f:
            config.write(f)
        print(f"   Utworzono domyślny plik konfiguracyjny. Używam wartości domyślnych.")
    else:
        config.read(CONFIG_FILE)

    settings = config['Settings']
    
    # Konwersja formatu 'rw-r--r--' na tryb numeryczny (chmod)
    # To jest złożony proces, dlatego dla uproszczenia w skrypcie będziemy porównywać z formatem tekstowym
    # Lepszym podejściem jest użycie os.chmod(..., mode) gdzie mode jest w oktalnym systemie np. 0o644
    
    return {
        'permissions': symbolic_to_octal(settings.get('suggested_permissions')),
        'trouble_chars': list(settings.get('troublesome_chars')),
        'substitute': settings.get('char_substitute'),
        'temp_exts': [e.strip() for e in settings.get('temp_extensions').split(',')],
        'target_dir': None # Docelowy katalog X - będzie ustawiony z argumentów
    }

# --- NARZĘDZIA PLIKOWE ---

def calculate_hash(file_path, algorithm='sha256'):
    """Oblicza sumę kontrolną pliku o dużym rozmiarze."""
    hasher = hashlib.new(algorithm)
    try:
        with open(file_path, 'rb') as f:
            while chunk := f.read(4096):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        # Prawa dostępu, błąd odczytu itp.
        return f"ERROR: {e}"

def get_file_stats(file_path):
    """Pobiera statystyki pliku: rozmiar, datę modyfikacji/utworzenia i uprawnienia."""
    stats = file_path.stat()
    return {
        'path': file_path,
        'size': stats.st_size,
        # Najczęściej data modyfikacji (mtime) i/lub data utworzenia (ctime)
        # Używamy mtime do wersji, ctime (lub birthtime na niektórych OS) do duplikatów.
        'mtime': stats.st_mtime, 
        'ctime': stats.st_ctime, 
        'permissions_octal': oct(stats.st_mode)[-3:], # np. '644' z '0o100644'
        # Reprezentacja tekstowa (rw-r--r--) wymaga bardziej złożonej funkcji
    }
    
def symbolic_to_octal(symbolic_permissions: str) -> str:
    """
    Converts a 9-character symbolic file permission string (e.g., 'rw-r--r--') 
    to its 3-digit octal representation (e.g., '644').
    
    Args:
        symbolic_permissions: The 9-character string representing permissions.
        
    Returns:
        The 3-digit octal string.
    
    Raises:
        ValueError: If the input string is not 9 characters long.
    """
    if len(symbolic_permissions) != 9:
        raise ValueError("Permission string must be exactly 9 characters long (e.g., 'rwxr-xr--').")

    # Map each permission letter to its octal value
    permission_map = {'r': 4, 'w': 2, 'x': 1, '-': 0}
    
    octal_parts = []
    
    # Iterate through the string in groups of three (Owner, Group, Others)
    for i in range(0, 9, 3):
        group_permissions = symbolic_permissions[i:i+3]
        octal_value = 0
        
        # Sum the values for 'r', 'w', and 'x' in the group
        for char in group_permissions:
            octal_value += permission_map.get(char, 0) # Use .get for safety
            
        octal_parts.append(str(octal_value))

    return "".join(octal_parts)

# --- GŁÓWNA LOGIKA SKANOWANIA I ANALIZY ---

def scan_directories(directories):
    """
    Skanuje podane katalogi i gromadzi informacje o wszystkich plikach.
    Zwraca: (lista_plików, mapa_hashy)
    """
    all_files = []
    hash_map = {} # { hash: [ {stats1}, {stats2}, ... ] }

    for dir_path in directories:
        print(f"🔎 Skanowanie katalogu: {dir_path}")
        
        # os.walk jest niezawodne do rekurencyjnego przechodzenia drzewa
        for root, _, files in os.walk(dir_path):
            for file_name in files:
                file_path = Path(root) / file_name
                
                # Użycie try-except do obsługi plików bez praw dostępu
                try:
                    stats = get_file_stats(file_path)
                    
                    # 1. Obliczanie hasha
                    file_hash = calculate_hash(file_path)
                    
                    stats['hash'] = file_hash
                    all_files.append(stats)
                    
                    # 2. Mapowanie hashy
                    if file_hash not in hash_map:
                        hash_map[file_hash] = []
                    hash_map[file_hash].append(stats)
                    
                except Exception as e:
                    print(f"🚫 Błąd dostępu/statystyk dla {file_path}: {e}")
                    continue
                    
    return all_files, hash_map

def analyze_and_suggest_actions(all_files, hash_map, config):
    """Analizuje zebrane dane i generuje listę proponowanych akcji."""
    suggestions = []
    
    # 1. Duplikaty (identyczna zawartość)
    for file_hash, file_list in hash_map.items():
        if "ERROR" in file_hash:
             continue # Pomijamy pliki, których nie udało się zahaszować

        if file_list[0]['size'] == 0:
            # Puste pliki zostaną obsłużone w kroku 2
            continue
            
        if len(file_list) > 1:
            # Wiele plików z tym samym hashem = duplikaty
            
            # Wyszukanie najstarszego pliku (wg. daty utworzenia/ctime)
            # Najstarsza data to najmniejsza wartość timestamp
            original_file = min(file_list, key=lambda x: x['ctime'])
            
            for file_stats in file_list:
                if file_stats['path'] != original_file['path']:
                    suggestions.append({
                        'type': 'DUPLICATE',
                        'path': file_stats['path'],
                        'suggestion': 'DELETE',
                        'reason': f"Identyczna zawartość ({file_hash}). Oryginał: {original_file['path']}",
                        'target_path': None
                    })
                # Jeśli to jest oryginalny plik, ale nie jest w katalogu X (target_dir)
                elif not str(original_file['path']).startswith(str(config['target_dir'])):
                    new_path = config['target_dir'] / original_file['path'].name
                    suggestions.append({
                        'type': 'MOVE_ORIGINAL',
                        'path': original_file['path'],
                        'suggestion': 'MOVE_TO_X',
                        'reason': f"Oryginalny plik ({file_hash}) powinien znaleźć się w X.",
                        'target_path': new_path
                    })


    # 2. Puste pliki, pliki tymczasowe, kłopotliwe nazwy i atrybuty
    for file_stats in all_files:
        path = file_stats['path']
        
        # Sprawdzanie, czy plik nie jest już oznaczony jako duplikat do skasowania
        if any(s['path'] == path and s['suggestion'] == 'DELETE' for s in suggestions):
            continue

        # a) Pliki puste
        if file_stats['size'] == 0:
            suggestions.append({
                'type': 'EMPTY_FILE',
                'path': path,
                'suggestion': 'DELETE',
                'reason': 'Plik pusty (rozmiar = 0)',
                'target_path': None
            })
            continue

        # b) Pliki tymczasowe
        if path.suffix in config['temp_exts'] or any(path.name.endswith(ext) for ext in config['temp_exts']):
            suggestions.append({
                'type': 'TEMP_FILE',
                'path': path,
                'suggestion': 'DELETE',
                'reason': f"Plik tymczasowy ({path.suffix})",
                'target_path': None
            })
            continue

        # c) Kłopotliwe nazwy
        original_name = path.name
        file_stem = path.stem       # Nazwa pliku bez rozszerzenia (np. 'raport.v1' dla 'raport.v1.pdf')
        file_suffix = path.suffix   # Rozszerzenie (np. '.pdf')
        
        new_stem = file_stem
        needs_rename = False
        
        # Iteracja po nazwie bazowej (bez rozszerzenia)
        for char in config['trouble_chars']:
            if char in new_stem:
                # Zamiana znaku
                new_stem = new_stem.replace(char, config['substitute'])
                needs_rename = True
        
        # Jeśli oryginalna nazwa pliku zawierała kropki, które nie były rozszerzeniem, 
        # i te kropki nie są traktowane jako kłopotliwe znaki w config, to problem z kropkami 
        # wewnątrz nazwy bazowej jest już obsłużony przez 'file_stem'. 
        
        # Jeśli użytkownik chciałby traktować '.' jako kłopotliwy znak w środku nazwy,
        # musi go uwzględnić w 'troublesome_chars' w pliku konfiguracyjnym. 
        # Dzięki użyciu path.stem kropka separatora rozszerzenia jest bezpieczna.

        if needs_rename:
            new_name = new_stem + file_suffix
            new_path = path.parent / new_name
            
            # Dodatkowy warunek, aby nie proponować zmiany, jeśli nowa nazwa jest taka sama
            if new_name != original_name:
                suggestions.append({
                    'type': 'RENAME',
                    'path': path,
                    'suggestion': 'RENAME',
                    'reason': f"Nazwa zawiera kłopotliwe znaki. Sugerowana nazwa: {new_name}",
                    'target_path': new_path
                })
        
        # d) Atrybuty (uproszczone: porównanie z oktalnym stringiem)
        target_permissions_octal = config['permissions'] # Zakładając, że to pole zostanie poprawnie obliczone
        if file_stats['permissions_octal'] != target_permissions_octal: # Używam 644 jako przykład
            suggestions.append({
                'type': 'PERMISSIONS',
                'path': path,
                'suggestion': 'CHMOD',
                'reason': f"Niepoprawne uprawnienia: {file_stats['permissions_octal']}. Sugerowane: 644",
                'target_path': None
            })

    # 3. Nowsze wersje (plik o tej samej nazwie, inna zawartość) - Bardzo trudne do automatycznej decyzji!
    # Ta logika wymagałaby grupowania plików nie po hashu, ale po samej nazwie bazowej.
    # Wymagałoby to stworzenia dodatkowej mapy { file_name: [stats1, stats2, ...] }
    
    return suggestions

def print_suggestions(suggestions):
    """Wyświetla propozycje akcji w czytelnej formie."""
    print("\n" + "="*50)
    print("📋 PODSUMOWANIE PROPOZYCJI PORZĄDKOWANIA")
    print("="*50)

    if not suggestions:
        print("🎉 Nie znaleziono żadnych problemów. Pliki są uporządkowane!")
        return

    for i, s in enumerate(suggestions):
        print(f"\n--- Akcja {i+1} ({s['type']}) ---")
        print(f"Plik:       {s['path']}")
        print(f"Problem:    {s['reason']}")
        print(f"SUGESTIA:   **{s['suggestion']}**", end="")
        if s['target_path']:
            print(f" -> {s['target_path']}")
        else:
            print("")

def perform_action(suggestion, config):
    """Wykonuje konkretną akcję na pliku i zwraca status operacji."""
    path = suggestion['path']
    action = suggestion['suggestion']
    target = suggestion.get('target_path')
    
    try:
        if action == 'DELETE':
            os.remove(path)
            print(f"✅ USUNIĘTO: {path}")
            return True
            
        elif action == 'MOVE_TO_X':
            # Używamy shutil.move, które obsługuje przenoszenie między systemami plików
            # Ważne: Tworzymy docelowy katalog, jeśli nie istnieje
            if target:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, target)
                print(f"✅ PRZENIESIONO: {path} -> {target}")
                return True
            
        elif action == 'RENAME':
            if target:
                path.rename(target) # rename działa także jako move, ale w obrębie tego samego FS
                print(f"✅ ZMIENIONO NAZWĘ: {path.name} -> {target.name}")
                return True
                
        elif action == 'CHMOD':
            # Zmiana uprawnień na wartość z konfiguracji
            os.chmod(path, config['permissions_octal'])
            print(f"✅ ZMIENIONO PRAW: {path} na {oct(config['permissions_octal'])[-3:]}")
            return True
            
        elif action == 'NO_ACTION':
            print(f"➡️ POMINIĘTO: {path}")
            return True
            
        else:
            print(f"❓ NIEZNANA AKCJA: {action} dla {path}")
            return False

    except FileNotFoundError:
        print(f"❌ BŁĄD: Plik nie istnieje ({path}). Prawdopodobnie już usunięty/przeniesiony.")
        return False
    except PermissionError:
        print(f"❌ BŁĄD: Brak uprawnień do wykonania akcji na {path}.")
        return False
    except Exception as e:
        print(f"❌ BŁĄD WYKONANIA: {e}")
        return False


GLOBAL_ACTION_MAP = {
    'y': 'ALWAYS_PERFORM', # Zawsze wykonaj sugerowaną akcję
    'n': 'ALWAYS_SKIP'     # Zawsze pomiń sugerowaną akcję
}

def get_user_choice(suggestion):
    """
    Pyta użytkownika o potwierdzenie SUGEROWANEJ akcji (Y/N/G - globalnie).
    Zwraca: 'PERFORM', 'NO_ACTION', 'ALWAYS_PERFORM', 'ALWAYS_SKIP'
    """
    action = suggestion['suggestion']
    
    prompt = (
        f"Czy chcesz wykonać akcję '{action}' na tym pliku? "
        f"[Y]es, [N]o, [G]lobalnie (na wszystkich tego typu): "
    )
    
    while True:
        try:
            choice = input(prompt).strip().lower()
            
            if choice in ['y', 'yes']:
                return 'PERFORM'
            elif choice in ['n', 'no']:
                return 'NO_ACTION'
            elif choice == 'g':
                global_choice = input(f"Zastosować akcję '{action}' globalnie (Y) czy pomijać globalnie (N)? [Y/N]: ").strip().lower()
                if global_choice == 'y':
                    return 'ALWAYS_PERFORM'
                elif global_choice == 'n':
                    return 'ALWAYS_SKIP'
                else:
                    print("Nieznana opcja. Spróbuj ponownie.")
            else:
                print("Nieznana opcja. Użyj Y, N lub G.")
                
        except EOFError:
            return 'NO_ACTION' 
            

def execute_actions(suggestions, config):
    """
    Interaktywny przebieg pętli akcji.
    """
    print("\n" + "#"*60)
    print("🤖 START FAZY WYKONYWANIA AKCJI (Interaktywny)")
    print("#"*60)
    
    # Słownik do przechowywania akcji globalnych dla każdego typu problemu
    global_actions = {} 
    
    for suggestion in suggestions:
        action_type = suggestion['type']
        current_suggestion = suggestion['suggestion']
        
        # 1. Sprawdzenie, czy dla tego typu problemu zdefiniowano akcję globalną
        if action_type in global_actions:
            action = global_actions[action_type]
            print(f"⚡ Globalna akcja: {action} dla typu {action_type}.")
        else:
            # 2. Wyświetlenie propozycji i zapytanie użytkownika
            print(f"\n--- PROPOZYCJA DLA PLIKU: {suggestion['path']} ---") 
            print(f"Problem: {suggestion['reason']}")
            print(f"SUGEROWANA AKCJA: **{current_suggestion}**")
            
            user_choice = get_user_choice(suggestion)
            
            # 3. Przetworzenie wyboru użytkownika
            if user_choice.startswith('ALWAYS_'):
                # Zapisanie akcji globalnej i wykonanie jej w obecnym przebiegu
                action = user_choice.split('ALWAYS_')[1] # np. PERFORM lub SKIP
                global_actions[action_type] = action
                print(f"🔥 Ustawiono akcję globalną '{action}' dla wszystkich typów '{action_type}'.")
            else:
                action = user_choice # Akcja lokalna: PERFORM lub NO_ACTION
        
        # 4. Wykonanie akcji
        if action == 'PERFORM' or (action == 'ALWAYS_PERFORM'):
             # Używamy sugerowanej akcji, bo użytkownik ją zatwierdził (Y/ALWAYS_Y)
             perform_action(suggestion, config)
        elif action == 'NO_ACTION' or (action == 'ALWAYS_SKIP'):
             print(f"➡️ POMINIĘTO: {suggestion['path']} na żądanie użytkownika.")
             
    print("\n" + "#"*60)
    print("✅ ZAKOŃCZONO FAZĘ WYKONYWANIA AKCJI.")
    print("#"*60)

# --- FUNKCJA GŁÓWNA (MODYFIKACJA) ---

def main():
    """Główna funkcja programu."""
    if len(sys.argv) < 2:
        print("Użycie: python file_organizer.py <katalog_docelowy_X> <katalog_Y1> [katalog_Y2...]")
        sys.exit(1)

    # Użycie funkcji z poprzedniego etapu
    target_dir = Path(sys.argv[1]).resolve()
    scan_dirs = [Path(d).resolve() for d in sys.argv[1:]]

    if not target_dir.is_dir():
        print(f"❌ Katalog docelowy X ({target_dir}) nie istnieje lub nie jest katalogiem.")
        sys.exit(1)

    config = load_config()
    config['target_dir'] = target_dir
    
    # Załóżmy, że wszystkie funkcje pomocnicze są zdefiniowane i działają:
    all_files, hash_map = scan_directories(scan_dirs) # Wymaga zaimplementowania scan_directories
    suggestions = analyze_and_suggest_actions(all_files, hash_map, config) # Wymaga zaimplementowania analyze_and_suggest_actions
    
    SORT_ORDER = {
        'TEMP_FILE': 1,       # Pliki tymczasowe
        'EMPTY_FILE': 2,      # Puste pliki
        'DUPLICATE': 3,       # Duplikaty (do usunięcia)
        'RENAME': 4,          # Zmiana nazwy
        'PERMISSIONS': 5,     # Zmiana uprawnień (CHMOD)
        'MOVE_ORIGINAL': 6,   # Przeniesienie (organizacja)
    }

    # Sortowanie propozycji na podstawie klucza zdefiniowanego w SORT_ORDER
    # Używamy .get z wysoką wartością domyślną (99), aby nieznane typy problemów znalazły się na końcu
    suggestions.sort(key=lambda s: SORT_ORDER.get(s['type'], 99))

    print_suggestions(suggestions) # Wyświetlenie wszystkich propozycji
    
    # NOWOŚĆ: Pytanie o kontynuację
    if suggestions and input("Czy chcesz rozpocząć interaktywną fazę wykonywania akcji? (t/n): ").strip().lower() == 't':
        execute_actions(suggestions, config)
    else:
        print("Anulowano wykonywanie akcji. Zakończenie pracy skryptu.")


if __name__ == "__main__":
    main()