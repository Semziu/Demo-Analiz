# Strona programu

Ten folder jest gotowym szkieletem prostej strony statycznej pod `AnalizatorDemCS2`.

## Do czego teraz sluzy ta strona

- jako landing page z przyciskiem pobierania,
- jako miejsce na opis programu,
- opcjonalnie jako dodatkowy download mirror.

Auto-aktualizacje programu sa teraz przygotowane pod `GitHub Releases`, a nie pod reczne `update.json`.

## Co wrzucasz na hosting

- `index.html`
- `styles.css`
- opcjonalnie folder `downloads/`

## Jak publikowac nowa wersje programu

Najwygodniejsza sciezka:

1. Publikujesz nowe wydanie na `GitHub Releases`.
2. Program sam sprawdza najnowszy release po `owner/repo`.
3. Strone mozesz zaktualizowac tylko wtedy, gdy chcesz odswiezyc opis albo przycisk pobierania.

## Gdzie jest pelny opis setupu

Sprawdz plik:

- `../GITHUB_RELEASES_SETUP.md`
