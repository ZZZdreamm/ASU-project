# Narzędzie do Uprzątania i Normalizacji Plików

### Wywołanie Skryptu

Skrypt musi być wywołany z co najmniej dwoma argumentami w wierszu poleceń:

1.  `<katalog_docelowy_X>`: **Główny katalog docelowy**.  Pliki uznane za "oryginalne" oraz wszystkie pozostałe pliki zostaną finalnie przeniesione do tego miejsca.
2.  `<katalog_Y1> [katalog_Y2...]`: **Katalogi źródłowe** do skanowania.

```bash
python3 main.py <katalog_docelowy_X> <katalog_Y1> [katalog_Y2...]
```

### Przykład pliku konfiguracyjnego **.clean_files** 
```
[Settings]
suggested_permissions = rw-r--r--
troublesome_chars = :;*?"$#`|\.
char_substitute = _
temp_extensions = .tmp,~,.bak,.DS_Store

```

### Przebieg skryptu

1. Skanowanie Katalogów - Skrypt przechodzi rekurencyjnie przez wszystkie wskazane katalogi (X i Y).
2. Wyświetlenie wszystkich potencjalnych akcji we wszystkich plikach
3. Możliwe akcje:
    - 'EMPTY_FILE' - plik jest pusty, USUŃ PLIK
    - 'TEMP_FILE' - plik jest tymczasowy, USUŃ PLIK
    - 'DUPLICATE' - plik jest duplikatem, USUŃ PLIK
    - 'VERSION_CONFLICT' - plik posiada nowszą wersję z tą sama nazwą, USUŃ STARY PLIK
    - 'RENAME' - plik ma dziwne znaki w nazwie, ZMIEŃ NAZWĘ
    - 'PERMISSIONS' - plik ma nietypowe pozwolenia, ZMIEŃ POZWOLENIA NA TYPOWE 
    - 'MOVE_ORIGINAL' - oryginał pliku jest w innym katalogu niż X, PRZENIEŚ GO DO KATALOGU X
4. Wybieranie akcji:
    - Tak, dla obecnego pliku
    - Nie, dla obecnego pliku
    - Tak, dla wszystkich tego typu akcji
    - Nie, dla wszystkich tego typu akcji
5. Po wszystkich akcjach:
    - Przenieś wszystkie pozostałe pliki do katalogu X
    - Usuń niepotrzebne, puste katalogi