# Teampanel

Visar teamets PR:er, pushar och plats i topplistan för Leonida och Xbox2Windows.
Sidan byggs om var 15:e minut av GitHub Actions från GitHubs publika API, och
senaste pushar hämtas direkt i webbläsaren var 2:a minut.

## Lägga till en medlem

Lägg till en rad under `members` i `team.json`:

```json
{"name": "Anna"}
```

Annas grenar ska då börja med `anna/`, till exempel `anna/fix-shader`.

## Rad till medlemmens Claude-prompt

> Alla grenar du skapar ska börja med `anna/`, till exempel `anna/kort-beskrivning`.
> Pusha till forken Vonsek/Leonida och öppna PR:er därifrån.

## Statusvarningar

- **AKTIV**: push eller PR-händelse senaste 60 minuterna
- **TYST**: 60 minuter till 3 timmar utan aktivitet
- **STANNAT**: mer än 3 timmar

Gränserna ändras med `quiet_minutes` och `stopped_minutes` i `team.json`.
