# PDFlex

Programma Windows per creare moduli PDF compilabili, confrontare due versioni di un documento e compilare un modulo in serie da Excel.

## Cosa fa

- **Moduli**: disegna campi (testo, data, caselle, scelte, menu, firma) oppure premi *Rileva campi* per crearli da righe `____` e caselle vuote. Ci sono anche:
  - formati (numero, importo in euro, email) e somme automatiche;
  - allineamento di più campi (Ctrl+clic per selezionarli) e griglia magnetica;
  - ordine Tab trascinabile e annulla/ripeti (Ctrl+Z / Ctrl+Y);
  - anteprima con dati di esempio.
- **Confronta**: mostra le differenze affiancate, con scorrimento sincronizzato. Puoi:
  - ignorare maiuscole e punteggiatura;
  - filtrare per tipo di modifica;
  - esportare in PDF (con l'elenco delle modifiche all'inizio) o in Excel.
  Le scansioni si confrontano solo se è installato [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) con la lingua italiana.
- **In serie**: modulo PDF + file Excel/CSV, un PDF compilato per ogni riga (le colonne si abbinano ai campi con lo stesso nome).

## Installare

1. `py -m venv .venv` e `.venv\Scripts\pip install -r requirements.txt`
2. `build.bat` crea `dist\PDFlex\PDFlex.exe`
3. Doppio clic su `Installa PDFlex.bat`: installa per il tuo utente (senza permessi di amministratore), aggiunge PDFlex al menu Start e ad "Apri con" sui PDF.

Si disinstalla da Impostazioni > App, oppure con `uninstall.ps1`.

Avvio da sorgente: `.venv\Scripts\python -m pdflex [file.pdf]`. Test: `.venv\Scripts\python -m pytest -q`

Licenza: usa PyMuPDF (AGPL). Va bene per uso personale/interno; per distribuirlo o venderlo serve pubblicare il codice o la licenza commerciale di Artifex.
