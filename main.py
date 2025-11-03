import os
import sys
import hashlib
import configparser
import shutil
import time
from pathlib import Path

CONFIG_FILE = Path(".clean_files")

def load_config():
    config = configparser.ConfigParser()
    
    config['Settings'] = {
        'suggested_permissions': 'rw-r--r--',
        'troublesome_chars': ':;*?"$#`|\\.', 
        'char_substitute': '_',
        'temp_extensions': '.tmp,~,.bak,.DS_Store',
    }
    
    if not CONFIG_FILE.exists():
        print(f"⚠️ Uwaga: Nie znaleziono pliku konfiguracyjnego: {CONFIG_FILE}")
        with open(CONFIG_FILE, 'w') as f:
            config.write(f)
        print(f"   Utworzono domyślny plik konfiguracyjny. Używam wartości domyślnych.")
    else:
        config.read(CONFIG_FILE)

    settings = config['Settings']
    
    return {
        'permissions': symbolic_to_octal(settings.get('suggested_permissions')),
        'trouble_chars': list(settings.get('troublesome_chars')),
        'substitute': settings.get('char_substitute'),
        'temp_exts': [e.strip() for e in settings.get('temp_extensions').split(',')],
        'target_dir': None
    }

def calculate_hash(file_path, algorithm='sha256'):
    hasher = hashlib.new(algorithm)
    try:
        with open(file_path, 'rb') as f:
            while chunk := f.read(4096):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        return f"ERROR: {e}"

def get_file_stats(file_path):
    stats = file_path.stat()
    return {
        'path': file_path,
        'size': stats.st_size,
        'mtime': stats.st_mtime, 
        'ctime': stats.st_ctime, 
        'permissions_octal': oct(stats.st_mode)[-3:],
    }
    
def symbolic_to_octal(symbolic_permissions: str) -> str:
    if len(symbolic_permissions) != 9:
        raise ValueError("Permission string must be exactly 9 characters long (e.g., 'rwxr-xr--').")

    permission_map = {'r': 4, 'w': 2, 'x': 1, '-': 0}
    
    octal_parts = []
    
    for i in range(0, 9, 3):
        group_permissions = symbolic_permissions[i:i+3]
        octal_value = 0
        
        for char in group_permissions:
            octal_value += permission_map.get(char, 0)
            
        octal_parts.append(str(octal_value))

    return "".join(octal_parts)

def scan_directories(directories):
    all_files = []
    hash_map = {}

    for dir_path in directories:
        print(f"🔎 Skanowanie katalogu: {dir_path}")
        
        for root, _, files in os.walk(dir_path):
            for file_name in files:
                file_path = Path(root) / file_name
                
                try:
                    stats = get_file_stats(file_path)
                    
                    file_hash = calculate_hash(file_path)
                    
                    stats['hash'] = file_hash
                    all_files.append(stats)
                    
                    if file_hash not in hash_map:
                        hash_map[file_hash] = []
                    hash_map[file_hash].append(stats)
                    
                except Exception as e:
                    print(f"🚫 Błąd dostępu/statystyk dla {file_path}: {e}")
                    continue
                    
    return all_files, hash_map

def analyze_and_suggest_actions(all_files, hash_map, config):
    suggestions = []
    
    name_map = {}
    for file_stats in all_files:
        filename = file_stats['path'].name
        if filename not in name_map:
            name_map[filename] = []
        name_map[filename].append(file_stats)
    
    for file_hash, file_list in hash_map.items():
        if "ERROR" in file_hash:
             continue

        if file_list[0]['size'] == 0:
            continue
            
        if len(file_list) > 1:
            
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
                elif not str(original_file['path']).startswith(str(config['target_dir'])):
                    new_path = config['target_dir'] / original_file['path'].name
                    suggestions.append({
                        'type': 'MOVE_ORIGINAL',
                        'path': original_file['path'],
                        'suggestion': 'MOVE_TO_X',
                        'reason': f"Oryginalny plik ({file_hash}) powinien znaleźć się w X.",
                        'target_path': new_path
                    })

    for file_stats in all_files:
        path = file_stats['path']
        
        if any(s['path'] == path and s['suggestion'] == 'DELETE' for s in suggestions):
            continue

        if file_stats['size'] == 0:
            suggestions.append({
                'type': 'EMPTY_FILE',
                'path': path,
                'suggestion': 'DELETE',
                'reason': 'Plik pusty (rozmiar = 0)',
                'target_path': None
            })
            continue

        if path.suffix in config['temp_exts'] or any(path.name.endswith(ext) for ext in config['temp_exts']):
            suggestions.append({
                'type': 'TEMP_FILE',
                'path': path,
                'suggestion': 'DELETE',
                'reason': f"Plik tymczasowy ({path.suffix})",
                'target_path': None
            })
            continue

        original_name = path.name
        file_stem = path.stem
        file_suffix = path.suffix
        
        new_stem = file_stem
        needs_rename = False
        
        for char in config['trouble_chars']:
            if char in new_stem:
                new_stem = new_stem.replace(char, config['substitute'])
                needs_rename = True
        
        if needs_rename:
            new_name = new_stem + file_suffix
            new_path = path.parent / new_name
            
            if new_name != original_name:
                suggestions.append({
                    'type': 'RENAME',
                    'path': path,
                    'suggestion': 'RENAME',
                    'reason': f"Nazwa zawiera kłopotliwe znaki. Sugerowana nazwa: {new_name}",
                    'target_path': new_path
                })
        
        target_permissions_octal = config['permissions']
        if file_stats['permissions_octal'] != target_permissions_octal:
            suggestions.append({
                'type': 'PERMISSIONS',
                'path': path,
                'suggestion': 'CHMOD',
                'reason': f"Niepoprawne uprawnienia: {file_stats['permissions_octal']}. Sugerowane: 644",
                'target_path': None
            })

    for file_name, file_list in name_map.items():
            if len(file_list) <= 1:
                continue
                
            all_same_hash = all(f['hash'] == file_list[0]['hash'] for f in file_list)
            
            if not all_same_hash:
                
                file_list.sort(key=lambda x: x['mtime'], reverse=True)
                newest_file = file_list[0]
                
                for file_stats in file_list[1:]:
                    path = file_stats['path']
                    
                    if any(s['path'] == path and s['suggestion'] == 'DELETE' for s in suggestions):
                        continue
                        
                    suggestions.append({
                        'type': 'VERSION_CONFLICT',
                        'path': path,
                        'suggestion': 'DELETE', 
                        'reason': f"Starsza wersja pliku. Nowszy plik (oryginał?) z: {time.ctime(newest_file['mtime'])} jest w {newest_file['path']}",
                        'target_path': None
                    })
                        
    return suggestions

def print_suggestions(suggestions):
    print("\n" + "="*50)
    print("📋 PODSUMOWANIE PROPOZYCJI PORZĄDKOWANIA")
    print("="*50)

    if not suggestions:
        print("🎉 Nie znaleziono żadnych problemów. Pliki są uporządkowane!")
        return

    for i, s in enumerate(suggestions):
        print(f"\n--- Akcja {i+1} ({s['type']}) ---")
        print(f"Plik:       {s['path']}")
        print(f"Problem:    {s['reason']}")
        print(f"SUGESTIA:   **{s['suggestion']}**", end="")
        if s['target_path']:
            print(f" -> {s['target_path']}")
        else:
            print("")

def perform_action(suggestion, config):
    path = suggestion['path']
    action = suggestion['suggestion']
    target = suggestion.get('target_path')
    
    try:
        if action == 'DELETE':
            os.remove(path)
            print(f"✅ USUNIĘTO: {path}")
            return True
            
        elif action == 'MOVE_TO_X':
            if target:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, target)
                print(f"✅ PRZENIESIONO: {path} -> {target}")
                return True
            
        elif action == 'RENAME':
            if target:
                path.rename(target)
                print(f"✅ ZMIENIONO NAZWĘ: {path.name} -> {target.name}")
                return True
                
        elif action == 'CHMOD':
            octal_mode = int(config['permissions'], 8)
            os.chmod(path, octal_mode)
            print(f"✅ ZMIENIONO PRAW: {path} na {config['permissions']}")
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

def get_user_choice(suggestion):
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
    print("\n" + "#"*60)
    print("🤖 START FAZY WYKONYWANIA AKCJI (Interaktywny)")
    print("#"*60)
    
    global_actions = {} 
    
    for suggestion in suggestions:
        action_type = suggestion['type']
        current_suggestion = suggestion['suggestion']
        
        if action_type in global_actions:
            action = global_actions[action_type]
            print(f"⚡ Globalna akcja: {action} dla typu {action_type}.")
        else:
            print(f"\n--- PROPOZYCJA DLA PLIKU: {suggestion['path']} ---") 
            print(f"Problem: {suggestion['reason']}")
            print(f"SUGEROWANA AKCJA: **{current_suggestion}**")
            
            user_choice = get_user_choice(suggestion)
            
            if user_choice.startswith('ALWAYS_'):
                action = user_choice.split('ALWAYS_')[1]
                global_actions[action_type] = action
                print(f"🔥 Ustawiono akcję globalną '{action}' dla wszystkich typów '{action_type}'.")
            else:
                action = user_choice
        
        if action == 'PERFORM' or (action == 'ALWAYS_PERFORM'):
             perform_action(suggestion, config)
        elif action == 'NO_ACTION' or (action == 'ALWAYS_SKIP'):
             print(f"➡️ POMINIĘTO: {suggestion['path']} na żądanie użytkownika.")
            
    print("\n" + "#"*60)
    print("✅ ZAKOŃCZONO FAZĘ WYKONYWANIA AKCJI.")
    print("#"*60)
    
    
def find_files_for_final_move(directories):
    files_to_move = []
    print("\n🔍 Szukanie plików do finalnego przeniesienia (rekurencyjnie w podkatalogach X i wszystkich Y)...")
    
    for dir_path in directories:
        if not dir_path.is_dir():
            print(f"⚠️ Ścieżka {dir_path} nie jest katalogiem. Pomijam.")
            continue
            
        print(f"Skanowanie: {dir_path}")
        
        for root, _, files in os.walk(dir_path):
            current_path = Path(root)
            for file_name in files:
                file_path = current_path / file_name
                
                if file_name != CONFIG_FILE.name:
                    files_to_move.append(file_path.resolve())
                        
    return sorted(list(set(files_to_move)))


def prompt_and_move_all_files(files_to_move, target_dir, directories):
    
    if not files_to_move:
        print("✅ Nie znaleziono żadnych plików do finalnego przeniesienia.")
        return

    print("\n" + "="*60)
    print(f"⭐ OSTATNI ETAP: PRZENOSZENIE PLIKÓW (FLATTENING) DO {target_dir.name}")
    print(f"Znaleziono {len(files_to_move)} plików do przeniesienia:")
    for f in files_to_move[:5]:
        print(f" - {f}")
    if len(files_to_move) > 5:
        print(f" - ... (oraz {len(files_to_move) - 5} innych)")
        
    choice = input("\nCzy chcesz przenieść TE wszystkie pliki do katalogu docelowego X? (Y/n): ").strip().lower()

    if choice == 'n':
        print("Anulowano finalne przenoszenie plików.")
        return

    print("\nRozpoczynanie przenoszenia...")
    moved_count = 0
    
    possible_empty_dirs = set() 
    
    for file_path in files_to_move:
        source_dir = file_path.parent
        if source_dir != target_dir:
            possible_empty_dirs.add(source_dir)
            
        target_path = target_dir / file_path.name
        
        try:
            if target_path.exists():
                print(f"⚠️ Konflikt nazwy: Plik {file_path.name} już istnieje w X. Pomijam przenoszenie {file_path}.")
                continue
                
            shutil.move(file_path, target_path)
            print(f"✅ Przeniesiono: {file_path} -> {target_path}")
            moved_count += 1
            
        except Exception as e:
            print(f"❌ Błąd przenoszenia {file_path}: {e}")
            
    possible_empty_dirs = possible_empty_dirs | set(directories)

    print("\nPróba usunięcia pustych katalogów źródłowych...")
    for directory in sorted(list(possible_empty_dirs), reverse=True):
        try:
            os.rmdir(directory)
            print(f"    Usunięto pusty katalog: {directory}")
        except OSError:
            pass

    print(f"\nOperacja zakończona. Przeniesiono {moved_count}/{len(files_to_move)} plików.")
    print("="*60)
    

def main():
    if len(sys.argv) < 2:
        print("Użycie: python file_organizer.py <katalog_docelowy_X> <katalog_Y1> [katalog_Y2...]")
        sys.exit(1)

    target_dir = Path(sys.argv[1]).resolve()
    scan_dirs = [Path(d).resolve() for d in sys.argv[1:]]

    if not target_dir.is_dir():
        print(f"❌ Katalog docelowy X ({target_dir}) nie istnieje lub nie jest katalogiem.")
        sys.exit(1)

    config = load_config()
    config['target_dir'] = target_dir
    
    all_files, hash_map = scan_directories(scan_dirs)
    suggestions = analyze_and_suggest_actions(all_files, hash_map, config)
    
    SORT_ORDER = {
        'EMPTY_FILE': 1,
        'TEMP_FILE': 2,
        'DUPLICATE': 3,
        'VERSION_CONFLICT': 4,
        'RENAME': 5,
        'PERMISSIONS': 6,
        'MOVE_ORIGINAL': 7,
    }

    suggestions.sort(key=lambda s: SORT_ORDER.get(s['type'], 99))

    print_suggestions(suggestions)
    
    if suggestions and input("Czy chcesz rozpocząć interaktywną fazę wykonywania akcji? (Y/n): ").strip().lower() != 'n':
        execute_actions(suggestions, config)
    else:
        print("Anulowano wykonywanie akcji. Zakończenie pracy skryptu.")
        
    y_dirs = scan_dirs[1:]
    
    try:
        x_subdirs = [p.resolve() for p in target_dir.iterdir() if p.is_dir()]
    except Exception as e:
        print(f"Błąd odczytu podkatalogów X: {e}. Traktuję listę jako pustą.")
        x_subdirs = []

    dirs_to_scan_for_move = x_subdirs + y_dirs
    
    files_to_move = find_files_for_final_move(dirs_to_scan_for_move)
    prompt_and_move_all_files(files_to_move, target_dir, dirs_to_scan_for_move)
    
    
    print("\n--- ZAKOŃCZENIE PRACY SKRYPTU ---")


if __name__ == "__main__":
    main()