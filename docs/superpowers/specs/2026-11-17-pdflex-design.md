# PDFlex — design

## Perché
Programma Windows per uso personale/interno con due funzioni:
1. **Editor moduli**: trasformare un PDF esistente in un modulo compilabile che altri riempiono in Acrobat o nel browser.
2. **Confronto**: confrontare due versioni di un contratto/documento di testo e vedere cosa è cambiato, come Draftable.

## Obiettivi
- Aprire un PDF, disegnare campi sulla pagina, salvare un PDF con campi AcroForm veri.
- Tipi di campo: testo (riga singola / multiriga), casella di spunta, scelta singola (radio), menu a tendina, data, firma.
- Confrontare due PDF affiancati: parole aggiunte in verde (documento nuovo), rimosse in rosso (documento vecchio), elenco delle modifiche cliccabile.
- Esportare il confronto come PDF con le pagine affiancate e le modifiche evidenziate.
- Un solo `.exe` per Windows.

## Non obiettivi (v1)
- Modificare il testo/contenuto del PDF.
- OCR di PDF scansionati (se un PDF non ha testo, il confronto lo dice e si ferma).
- Firma digitale vera (il campo firma è vuoto, si firma in Acrobat).
- Calcoli/script tra campi, validazioni complesse.
- Confronto di formattazione (grassetto, font) o grafica.
- Annulla/ripeti.
- Unire/dividere/ruotare pagine.

## Anti-obiettivi
- Un modulo salvato che non funziona in Acrobat Reader, Edge o Chrome.
- Sovrascrivere l'originale senza volerlo: si salva sempre con "Salva con nome".
- Un confronto che segna come modificato un intero paragrafo per una virgola.

## Vincoli
- Windows 11, Python 3.14 (già installato, comando `py`).
- Licenza AGPL di PyMuPDF accettata: uso personale/interno.
- Interfaccia in italiano.

## Approcci considerati
- **A. Python + PySide6 + PyMuPDF** — scelto. Una sola libreria per vedere le pagine, creare i campi (firma compresa) ed estrarre le parole con la loro posizione; il diff usa `difflib` di Python. Meno codice. Limite: AGPL.
- **B. Electron + pdf.js + pdf-lib** — tutto MIT, ma eseguibile ~150 MB, niente campi firma, più codice per l'interfaccia.
- **C. C# / WPF** — più nativo, ma senza librerie gratuite che facciano bene sia moduli sia visualizzazione (iText è AGPL, le altre a pagamento).

## Design

Finestra unica con due schede: **Editor moduli** e **Confronta**.

### Editor moduli
- Barra strumenti: Apri, Salva con nome, zoom, pagina prec./succ., e un pulsante per ogni tipo di campo + "Seleziona".
- Pagina mostrata come immagine (PyMuPDF `get_pixmap`) in una `QGraphicsView`; i campi sono rettangoli sopra di essa.
- Con un tipo di campo attivo, trascinando si disegna il campo. In modalità Seleziona: clic per selezionare, trascina per spostare, maniglie per ridimensionare, `Canc` elimina, `Ctrl+D` duplica.
- Pannello proprietà a destra per il campo selezionato: nome (unico, proposto in automatico es. `testo_1`), obbligatorio, valore predefinito, dimensione carattere; opzioni (una per riga) per il menu a tendina; nome gruppo + valore per il radio.
- I campi già presenti nel PDF vengono caricati e sono modificabili.
- Al salvataggio: si rimuovono i widget originali e si riscrivono tutti con `fitz.Widget` (`page.add_widget`). Data = campo testo con formato `AFDate_FormatEx("dd/mm/yyyy")`. Radio = più widget con lo stesso nome di campo e valori diversi.

### Confronta
- Due pulsanti "Apri originale" / "Apri nuova versione"; due viste affiancate con tutte le pagine una sotto l'altra.
- Estrazione parole da tutte le pagine con `page.get_text("words")` (testo + rettangolo).
- Diff a livello di parola con `difflib.SequenceMatcher(autojunk=False)` sul testo normalizzato (spazi collassati, maiuscole conservate).
- `delete` → rettangoli rossi sull'originale; `insert` → verdi sulla nuova versione; `replace` → entrambi.
- Pannello laterale con l'elenco delle modifiche ("Rimosso: …", "Aggiunto: …", "Modificato: … → …") con numero di pagina; clic → entrambe le viste scorrono alla modifica. Pulsanti modifica precedente/successiva.
- Pulsante **Esporta PDF**: crea un nuovo PDF dove ogni pagina affianca pagina N dell'originale (sinistra) e pagina N della nuova versione (destra), copiate come vettoriali con `show_pdf_page`, con i rettangoli rossi/verdi disegnati sopra. Se un documento ha più pagine dell'altro, il lato mancante resta bianco. In testa all'export, una o più pagine di riepilogo: nomi dei due file, data, numero di modifiche, e l'elenco delle modifiche (stesso testo del pannello laterale, con numero di pagina).
- PDF senza testo estraibile → messaggio "Il PDF non contiene testo (forse è una scansione): OCR non supportato".

### Struttura file
- `pdflex/app.py` — finestra principale, schede, avvio.
- `pdflex/editor.py` — scheda editor.
- `pdflex/compare.py` — scheda confronto + funzione di diff pura (testabile senza interfaccia).
- `tests/test_compare.py`, `tests/test_editor_save.py` — controlli minimi: diff su testi noti; salvataggio di un PDF con un campo per tipo e rilettura dei widget.
- `requirements.txt`, `build.bat` (PyInstaller `--onefile --windowed`).

## Lasciato al costruttore
- Aspetto preciso di barre e pannelli, colori di selezione, scorciatoie oltre a quelle sopra.
- Risoluzione di rendering e gestione zoom.
- Come mostrare le maniglie di ridimensionamento.

## Decisioni prese da Claude (da verificare)
- Niente annulla/ripeti in v1.
- Il confronto distingue maiuscole/minuscole e ignora gli spazi.
- Data in formato `gg/mm/aaaa`.
