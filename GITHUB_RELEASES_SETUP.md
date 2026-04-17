# GitHub Releases - szybki setup

## Co juz jest gotowe

- Program potrafi sprawdzac najnowszy `GitHub Release`.
- Szuka pliku `DemoAnaliz.exe` w najnowszym wydaniu.
- Po znalezieniu nowszej wersji pobiera ja i podmienia po zamknieciu aplikacji.
- Workflow `.github/workflows/release.yml` buduje `.exe` i wrzuca go do releasu po pushu taga `v*`.

## Co musisz zrobic raz

1. Wrzuc ten projekt do repozytorium GitHub.
2. W aplikacji otworz `Ustawienia`.
3. Wpisz:
   - `GitHub owner`: nazwa konta lub organizacji
   - `Repozytorium`: nazwa repo
   - `Plik w release`: zostaw `DemoAnaliz.exe`
4. Zapisz ustawienia.

Od tego momentu uzytkownik nie musi juz wklejac zadnego URL.

## Jak wydac nowa wersje

1. Zmien `APP_VERSION` w `Test.py`.
2. Wypchnij commit do GitHub.
3. Utworz tag w formacie `v1.5.1`, `v1.6.0` itd.
4. Po wypchnieciu taga GitHub Actions zbuduje nowe `.exe` i doda je do `Releases`.

## Jak wersja jest wykrywana

- Program czyta najnowszy release z:
  `https://api.github.com/repos/OWNER/REPO/releases/latest`
- Wersje bierze z `tag_name`, np. `v1.5.1`
- Porownuje ja z lokalnym `APP_VERSION`

## Wazne

- Nazwa assetu w releasie musi byc zgodna z tym, co wpiszesz w aplikacji.
- Najbezpieczniej zostawic `DemoAnaliz.exe`.
- Jesli w releasie bedzie tylko jeden plik `.exe`, program sprobuje uzyc go automatycznie.
