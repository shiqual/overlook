# OverLook — Webinterface voor OverMesh berichten

OverLook is een standalone webinterface om Meshtastic (MT) en MeshCore (MC) berichten te bekijken, doorzoeken en filteren. Het leest direct de SQLite databases van [OverMesh](https://github.com/Slofi/overmesh) — geen cloud, geen account, geen gedoe.

## Features

- **Bekijk berichten per kanaal** — selecteer een kanaal in de zijbalk om alleen die berichten te zien
- **Doorzoek alle berichten** — zoek op tekst (LIKE), filter op datum/tijd, of kanaalbereik
- **Live modus** — automatisch nieuwe berichten laden om de 3 seconden (aanpasbaar)
- **DM/niet-DM filter** — schakel tussen alle berichten, alleen kanaalberichten, of alleen DMs
- **Kanaalnamen** — geef zelf namen aan kanalen (bijv. "Primary (NL)", "Off-Topic"), opgeslagen in config
- **Zowel MT als MC** — herkent automatisch Meshtastic en MeshCore databases
- **Meerdere databases** — als je meerdere radios hebt, zie je ze allemaal in de kieslijst
- **Donker theme** — past bij OverMesh
- **Accent kleur** — volledig aanpasbaar via instellingen
- **Remote toegankelijk** — draait op `0.0.0.0`, dus bereikbaar vanaf elk apparaat in je netwerk

## Installatie

```bash
git clone https://github.com/shiqual/overlook.git
cd overlook
pip install flask
```

### Configuratie

De data map wordt automatisch gevonden via:

1. `OVERMESH_DATA_DIR` omgevingsvariabele, of
2. De map `../overmesh` t.o.v. het script, of
3. Handmatig instellen in de webinterface onder **Instellingen → OverMesh data map**

### Gebruik

```bash
python3 overlook_web.py
```

Open daarna `http://<ip-van-server>:8085` in je browser.

### Als systemd service (Linux)

```bash
cp overlook.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now overlook.service
```

## Instellingen

Alle instellingen zijn aanpasbaar via de webinterface (**Instellingen** tab) en worden opgeslagen in `overlook_config.json`:

| Instelling | Standaard | Omschrijving |
|---|---|---|
| OverMesh data map | `../overmesh` | Waar de `overmesh_*.db` bestanden staan |
| Ververs interval | 3s | Hoe vaak nieuwe berichten ophalen in Live modus |
| Max berichten | 2000 | Maximum aantal berichten in het geheugen |
| Standaard limiet | 500 | Hoeveel berichten per keer laden |
| Live modus bij start | Aan | Of Live automatisch start |
| Accent kleur | `#4ade80` | Kleur van de interface |
| Kanaalnamen | — | Zelf te benoemen kanalen (bv. 0 = Primary) |

## Schermafbeeldingen

*Nog toe te voegen*

## Waarom dit?

OverMesh zelf heeft geen ingebouwde zoek- of filterfunctie voor berichtgeschiedenis. OverLook vult dat aan: een simpele, snelle webinterface die je naast OverMesh kunt draaien om door de opgeslagen berichten te bladeren.

## Licentie

MIT
