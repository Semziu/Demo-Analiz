# Szybkie wrzucenie projektu na GitHub

## Docelowe repo

Zaloz repo o nazwie:

- `Demo-Analiz`

na koncie:

- `Semziu`

## Najprostsza metoda bez gita

1. Wejdz na GitHub i zaloz nowe repo `Demo-Analiz`.
2. Kliknij `uploading an existing file`.
3. Wrzuc zawartosc tego folderu projektu.
4. Commituj pliki do branchu `main`.
5. Po wrzuceniu utworz pierwszy release o tagu `v1.5.0`.
6. Do releasu dodaj plik `DemoAnaliz.exe`.

## Co trzeba wrzucic

- `Test.py`
- `.github/workflows/release.yml`
- `README.md`
- `GITHUB_RELEASES_SETUP.md`
- `website/`

Nie musisz wrzucac:

- `build/`
- `dist/`
- `live_coach_sessions/`
- `biblioteka_dem/`

Wyjatek:

- jesli chcesz od razu dodac gotowy plik do pobrania, wrzuc osobno `dist/DemoAnaliz.exe` jako asset releasu, a nie jako zwykly plik repo.
