# 💍 Middle-earth MTG Management

Applicazione privata e self-hosted per la collezione **Magic: The Gathering**
di Terra di Mezzo (*The Hobbit* + *The Lord of the Rings*): traccia la collezione
fisica, calcola le carte mancanti e il completamento dei set, gestisce una
wishlist d'acquisto e costruisce il mazzo Commander da 100 carte di
**Aragorn, the Uniter**.

Segue lo **standard di hosting del Family Portal**: gira sul Raspberry Pi
(`192.168.1.129`) in un container Docker, dietro il reverse proxy Caddy, con
deploy automatico via **Jenkins** — esattamente come le altre app (MyGarage,
Audible Converter, ecc.).

## Stack

| Layer      | Scelta                                        |
| ---------- | --------------------------------------------- |
| Backend    | Python **FastAPI**                            |
| Database   | **SQLite** (volume Docker `/app/data`)        |
| Frontend   | HTML/CSS/JS statico servito dal backend       |
| Deploy     | **Docker** + **Jenkins** (pipeline `Jenkinsfile`) |
| Porta      | **8094** (registro porte del Family Portal)   |
| Reverse proxy | Caddy → `http://192.168.1.129/mtg/`        |

## Struttura

```
MTG_Management/
├── app/
│   ├── backend/
│   │   ├── main.py          # FastAPI: API REST + serve la UI statica
│   │   ├── database.py      # engine/sessione SQLite
│   │   ├── models.py        # cards, wishlist, deck_cards, import_log
│   │   ├── schemas.py       # modelli Pydantic
│   │   ├── importer.py      # import XLSX/CSV (validazione + merge duplicati)
│   │   ├── seed.py          # import automatico del workbook al primo avvio
│   │   ├── requirements.txt
│   │   └── seed/collection_seed.csv   # 209 carte estratte dal workbook
│   └── frontend/            # index.html, styles.css, app.js
├── Dockerfile
├── docker-compose.yml
├── Jenkinsfile              # pipeline CI/CD (build + deploy container sul Pi)
├── .env.example
└── Middle_Earth_MTG_Collection_*.{xlsx,docx}   # PRD + workbook seed originali
```

## Funzionalità (MVP dal PRD)

- **Dashboard**: completamento unico/totale, copie, mancanti per rarità/colore,
  gap Rare/Mythic rilevanti per Aragorn, valore wishlist, stato mazzo, ultimo import.
- **Collection**: ricerca (nome/oracle), filtri per set/rarità/posseduto, modifica quantità.
- **Sets**: completamento separato *The Hobbit* / *The Lord of the Rings*.
- **Wishlist**: scopo, priorità (P1–P4/Watch), prezzo target e massimo, stato.
- **Aragorn Commander**: builder singleton, validazione (100 carte, singleton,
  colour identity Bant, restrizione set di progetto), stato Owned/Need per slot.
- **Import/Export**: import XLSX/CSV con report (added/updated/unchanged/rejected/issues),
  export CSV e **backup** del database SQLite.

Il workbook `Middle_Earth_MTG_Collection_Master_Template.xlsx` è la **sorgente
autoritativa**: al primo avvio, se il DB è vuoto, viene importato da
`app/backend/seed/collection_seed.csv` (207 carte uniche; 2 identità duplicate
del workbook vengono unite e segnalate).

## Sviluppo locale

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app\backend\requirements.txt
cd app\backend
..\..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8094
# apri http://127.0.0.1:8094/
```

Il DB SQLite viene creato in `app/backend/data/mtg.db` (ignorato da git).
Override con la variabile `MTG_DATA_DIR`.

## Deploy sul Raspberry Pi

### Opzione A — Jenkins (raccomandata, come le altre app)

1. **Push** del repo su GitHub (`main`) con il `Jenkinsfile`.
2. Sul Pi, crea il job copiando lo script init in Jenkins (già pronto nel Family
   Portal: [`scripts/pi/mtg-collection-setup.groovy`](../FamilyPortal/scripts/pi/mtg-collection-setup.groovy)):

   ```bash
   J=ci_cd_validation_jenkins_1
   docker exec "$J" sh -c "mkdir -p /var/jenkins_home/init.groovy.d"
   docker cp mtg-collection-setup.groovy "$J:/var/jenkins_home/init.groovy.d/"
   docker restart "$J"
   ```

   Jenkins crea il job `mtg-collection`, fa il checkout del repo ed esegue la
   pipeline: **build** dell'immagine `mtg-collection:latest` e **deploy** del
   container sulla porta **8094** con volume dati `mtg-collection-data` e
   `--restart unless-stopped` (riparte da solo al reboot del Pi).

3. Segui la prima build su `http://192.168.1.129:8080/job/mtg-collection/`.

### Opzione B — Docker Compose (manuale)

```bash
cp -r MTG_Management ~/apps/mtg-collection
cd ~/apps/mtg-collection
docker compose up -d
docker compose ps
```

### Reverse proxy + dashboard

Già configurati nel Family Portal:

- **Caddyfile**: rotta `handle_path /mtg/*` → `localhost:8094` e sottodominio
  `mtg.borrellofamily.co.uk`. Ricarica con `.\deploy.ps1 -SetupCaddy` dal repo FamilyPortal.
- **services.json**: voce `mtg-collection` (categoria *Collections*), così l'app
  compare nella dashboard con lo stato live.

Accessi:

| Contesto            | URL                                    |
| ------------------- | -------------------------------------- |
| LAN (porta diretta) | `http://192.168.1.129:8094/`           |
| LAN (via Caddy)     | `http://192.168.1.129/mtg/`            |
| Esterno (tunnel)    | `http://mtg.borrellofamily.co.uk/`     |

## API (REST/JSON)

```
GET    /health
GET    /api/sets
GET    /api/sets/{set_name}/completion
GET    /api/cards?set=&owned=&q=&rarity=
PATCH  /api/collection/{card_id}          { "quantity": n }
GET    /api/collection/summary
GET    /api/wishlist
POST   /api/wishlist
PATCH  /api/wishlist/{id}
DELETE /api/wishlist/{id}
GET    /api/decks/aragorn
POST   /api/decks/aragorn/cards
PATCH  /api/decks/aragorn/cards/{slot_id}
DELETE /api/decks/aragorn/cards/{slot_id}
GET    /api/decks/aragorn/validation
POST   /api/import                        (multipart: file XLSX/CSV)
GET    /api/export/collection.csv
GET    /api/backup                         (download del DB SQLite)
```

## Backup

- `GET /api/backup` scarica una copia del file SQLite in qualsiasi momento.
- `GET /api/export/collection.csv` esporta la collezione (recupero senza il DB).
