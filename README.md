# PDFlex

Programma Windows per creare moduli PDF compilabili, confrontare due versioni di un documento e compilare un modulo in serie da Excel.

## Cosa fa

- **Editor**: sistema la grafica del PDF prima di farne un modulo. Prendi e sposta linee, rettangoli, forme, immagini e testi della pagina, cambiane misure, colori e parole, o aggiungi testo, linee, rettangoli e immagini. Campi e pulsanti non si vedono e restano intatti. Una striscia gialla avvisa di ciò che non si può modificare (testo disegnato come forme, sfumature, ritagli), e una modifica che toccherebbe altro viene annullata. *Salva PDF* crea `<nome>_modificato.pdf`; *Apri in Moduli* lo passa a Moduli per sistemare i campi.
- **Moduli**: disegna campi (testo, data, caselle, scelte, menu, firma) oppure premi *Rileva campi* per crearli da righe `____` e caselle vuote. Ci sono anche:
  - formati (numero, importo, percentuale con decimali a scelta; data e ora; CAP, codice fiscale, telefono, email), somme automatiche, carattere, dimensione, colore e allineamento del testo;
  - *Salva modulo*: il PDF con i campi come li vedi, da riaprire in PDFlex o da far compilare a una persona;
  - *Esporta per gestionale*: una copia con i campi vuoti, senza bordi né pulsanti, per un programma che li riempie da solo;
  - allineamento di più campi (Ctrl+clic per selezionarli) e griglia magnetica;
  - ordine Tab trascinabile e annulla/ripeti (Ctrl+Z / Ctrl+Y);
  - anteprima con dati di esempio;
  - *Compila*: riempi i campi a mano, con il modulo compilato accanto, e salvane una copia.
- **Confronta**: mostra le differenze affiancate, con scorrimento sincronizzato. Puoi:
  - ignorare maiuscole e punteggiatura;
  - filtrare per tipo di modifica;
  - esportare in PDF (con l'elenco delle modifiche all'inizio) o in Excel.
  Le scansioni si confrontano solo se è installato [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) con la lingua italiana.
- **In serie**: modulo PDF + file Excel/CSV, un PDF compilato per ogni riga (le colonne si abbinano ai campi con lo stesso nome), oppure un solo PDF con tutti i moduli da stampare.
- **Pagine**: unisci PDF, riordina, ruota, elimina, estrai e dividi le pagine. Nelle *Opzioni* puoi aggiungere numeri di pagina, intestazione e filigrana (BOZZA, COPIA) e ridurre la dimensione del file.

## Installare

1. `py -m venv .venv` e `.venv\Scripts\pip install -r requirements.txt`
2. `build.bat` crea `dist\PDFlex\PDFlex.exe`
3. Doppio clic su `Installa PDFlex.bat`: installa per il tuo utente (senza permessi di amministratore), aggiunge PDFlex al menu Start e ad "Apri con" sui PDF.

Si disinstalla da Impostazioni > App, oppure con `uninstall.ps1`.

Avvio da sorgente: `.venv\Scripts\python -m pdflex [file.pdf]`. Test: `.venv\Scripts\python -m pytest -q`

Licenza: usa PyMuPDF (AGPL). Va bene per uso personale/interno; per distribuirlo o venderlo serve pubblicare il codice o la licenza commerciale di Artifex.
