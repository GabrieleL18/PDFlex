# Sezione "Editor": modificare la grafica di un PDF

## Perché

Prima di trasformare un PDF in modulo, l'utente deve spesso sistemarne la grafica: spostare linee, aggiungere componenti, aggiungere scritte. Oggi deve farlo con un altro programma. La nuova sezione **Editor** lo fa dentro PDFlex, "come in Figma": apri il PDF, vedi gli oggetti della pagina, li prendi e li sposti. Poi passi il risultato a **Moduli** per sistemare i campi.

## Obiettivi

- Aprire un PDF e vederne gli oggetti, selezionabili uno per uno: linee, rettangoli, altre forme, immagini e blocchi di testo.
- I campi compilabili e i pulsanti **non** si vedono e non si toccano, ma restano nel PDF salvato.
- Spostare, ridimensionare ed eliminare gli oggetti esistenti.
- Aggiungere componenti: **testo, linea, rettangolo** (vuoto o pieno) e **immagine**.
- Cambiare le proprietà dell'oggetto selezionato: posizione e misure, colore del tratto e del riempimento, spessore, carattere, dimensione e colore del testo.
- Avvisare quando qualcosa non si riesce a convertire o a modificare (vedi "Avvisi").
- Annullare e ripetere (Ctrl+Z / Ctrl+Y).
- Salvare un PDF nuovo e aprirlo in Moduli con un clic.

## Fuori dallo scopo (per ora)

- Pannello dei livelli, gruppi, rotazione libera degli oggetti, allineamenti e guide intelligenti.
- Libreria di componenti riutilizzabili. I "blocchi salvati" di Moduli restano solo per i campi.
- Spostare oggetti da una pagina all'altra, oppure aggiungere o togliere pagine (lo fa già la sezione Pagine).
- Riconoscere il testo disegnato come forme (OCR).

## Da evitare (sarebbe un fallimento anche se "funziona")

- Cambiare l'aspetto di parti della pagina che l'utente non ha toccato.
- Perdere campi compilabili, pulsanti o il loro JavaScript.
- Far sparire contenuto senza dirlo: ogni cosa che PDFlex non sa gestire resta com'era e viene segnalata.

## Strade considerate

| Strada | Come funziona | Pro e contro | Scelta |
|---|---|---|---|
| **A. Modifica sul posto** | La pagina resta l'originale; cambia solo l'oggetto toccato, che viene tolto e ridisegnato | Tutto ciò che non tocchi resta identico e campi e pulsanti restano intatti; il testo esistente si cambia solo con le lettere presenti nel suo carattere | **Scelta dall'utente** |
| B. Ricostruire la pagina | Tutto convertito in oggetti, pagina riscritta a ogni salvataggio | Massima libertà, ma caratteri e sfumature possono cambiare anche dove non si è toccato niente | Scartata: va contro "non cambiare ciò che non tocchi" |
| C. Solo aggiunte sopra | L'originale è uno sfondo fisso, si può solo aggiungere | Semplicissima, ma "spostare una linea" vorrebbe dire coprirla di bianco | Scartata: non sposta davvero le linee |

## Come funziona (strada A)

### Il documento di lavoro

L'Editor apre una **copia in memoria** del PDF, che contiene sempre lo stato attuale. Ogni modifica viene applicata subito a questa copia, poi PDFlex rilegge gli oggetti della pagina e la ridisegna sullo schermo: quello che vedi è sempre il PDF vero.

Annulla e ripeti tengono una copia della pagina prima di ogni modifica. *(Scelta del costruttore: copia dell'intero documento con `tobytes`, oppure solo della pagina, purché resti veloce su un documento di 50 pagine.)*

La pagina sullo schermo è disegnata **senza annotazioni** (`annots=False`), così campi e pulsanti non si vedono.

### Riconoscere gli oggetti (all'apertura e dopo ogni modifica)

Ogni oggetto ha un tipo, un riquadro, uno stato (modificabile, solo spostabile, bloccato) e i dati per ridisegnarlo.

- **Linee:** i tratti diritti di `page.get_drawings()`. Contano come linee anche i rettangoli pieni sottilissimi (meno di 2 pt in una direzione), che molti programmi usano al posto delle linee.
- **Rettangoli:** i percorsi fatti di un solo rettangolo (`re`, oppure 4 lati chiusi), con tratto e/o riempimento.
- **Altre forme:** gli altri percorsi (curve, poligoni). Si possono spostare, ridimensionare ed eliminare, non si modificano punto per punto.
- **Immagini:** da `page.get_image_info(xrefs=True)`. Si possono spostare, ridimensionare ed eliminare.
- **Testo:** i blocchi di `page.get_text("dict")`, uno per riga. Si possono spostare ed eliminare; le parole si possono cambiare solo nei limiti spiegati sotto.
- **Testo disegnato come forme**, come nella busta paga: tante piccole forme piene vicine fra loro. Si raggruppano in parole o righe e si trattano come "altre forme" (si spostano, non si modificano), con l'avviso.

### Modificare un oggetto esistente

1. Si toglie l'oggetto dalla pagina con una *redaction* che agisce solo sul suo tipo:
   - per una forma: `graphics=PDF_REDACT_LINE_ART_REMOVE_IF_COVERED`, `text=NONE`, `images=NONE`;
   - per il testo: solo `text=REMOVE`;
   - per un'immagine: solo `images=REMOVE`.

   Provato: togliere così una linea lascia intatti testo, altre linee, campi e pulsanti.
2. **Effetto collaterale:** una redaction grande, per esempio per spostare un riquadro, toglie anche le forme che stanno tutte dentro l'area. PDFlex se le segna prima (da `get_drawings`) e dopo le **ridisegna identiche**.
3. L'oggetto viene ridisegnato nel nuovo punto o con le nuove misure:
   - una forma, con `Shape` e gli stessi tratto, riempimento, spessore, tratteggio e trasparenza;
   - un'immagine, con `insert_image` dello stesso xref;
   - il testo, con `insert_text` usando il carattere originale, estratto con `extract_font`.
4. **Ordine di sovrapposizione:** le forme ridisegnate vanno **sotto** il resto (`commit(overlay=False)`), nel loro ordine originale. Così uno sfondo colorato spostato non copre il testo che gli stava sopra. Gli oggetti aggiunti dall'utente vanno **sopra** tutto.

### Cambiare le parole di un testo esistente

Spesso il PDF contiene solo le lettere usate del carattere: il carattere di Modelli.pdf ne ha 85. PDFlex controlla ogni lettera nuova con `Font.has_glyph`.

- Se ci sono tutte, il testo resta nel suo carattere.
- Se ne manca qualcuna, compare l'avviso "Il carattere di questo testo non ha le lettere: €. Uso Helvetica per questo testo?", con le scelte **Sì** e **Annulla**. *(Scelta mia: avviso con scelta, non un blocco. Correggimi se preferisci bloccare.)*

### Componenti nuovi

Una palette a sinistra, come in Moduli, con:

- **Testo:** un clic sulla pagina crea una casella di testo e si scrive subito;
- **Linea:** si trascina dal punto iniziale a quello finale; con Maiusc resta orizzontale o verticale;
- **Rettangolo:** si trascina;
- **Immagine:** si sceglie un file PNG o JPG e si trascina il riquadro.

Caratteri disponibili per il testo nuovo: quelli che Moduli già offre (`FONTS` in `fields.py`).

### Avvisi

All'apertura di ogni pagina, se qualcosa non si può gestire, compare una striscia gialla in cima. Per esempio: *"Pagina 1: 772 forme sono testo disegnato come forme (si spostano ma non si modificano); 2 sfumature restano come sono."*

Gli oggetti che non si toccano sono:

- sfumature e motivi;
- contenuto ritagliato (clip);
- oggetti dentro moduli grafici annidati che PDFlex non riesce a separare.

Si vedono con un lucchetto quando ci passi sopra, e il tooltip spiega perché.

Se una modifica riesce solo in parte, compare un avviso e la modifica viene annullata. Per esempio se, dopo aver tolto un oggetto, una forma non si può ridisegnare identica. Non si salva mai una pagina rovinata.

### Interfaccia

È una nuova voce **Editor** nella barra di sinistra, prima di Moduli. La struttura riprende quella di Moduli, così chi conosce l'una sa usare l'altra:

```
[palette]  [miniature] [      pagina       ]  [proprietà]
Seleziona   ▢ 1         ┌───────────────┐    Linea
Testo       ▢ 2         │  ── ← selez.  │    X 72  Y 104
Linea       ▢ 3         │               │    Larghezza 228
Rettangolo              └───────────────┘    Colore ■  Spessore 1
Immagine
```

- **Barra in alto:** Apri, Salva PDF, Annulla, Ripeti, **Apri in Moduli**, pagina precedente e successiva, zoom.
- **Pagina:** un clic seleziona; Ctrl+clic o il trascinamento sul vuoto selezionano più oggetti. Gli oggetti si trascinano per spostarli, e le maniglie agli angoli li ridimensionano (alle linee servono solo gli estremi). Canc elimina; le frecce spostano di 1 pt, Maiusc+frecce di 10 pt.
- **Proprietà:** cambiano in base al tipo di oggetto selezionato.
- **Apri in Moduli:** se ci sono modifiche non salvate chiede di salvarle, poi apre il file in Moduli e passa a quella scheda.

### Salvataggio

**Salva PDF** scrive la copia di lavoro in un nuovo file, proponendo il nome `<nome>_modificato.pdf`. Usa `write_atomic`, come gli altri salvataggi. Campi e pulsanti ci sono perché non sono mai stati toccati.

## Dove sta il codice (scelta mia, seguendo il progetto)

- `pdflex/design.py`: la parte senza interfaccia. Contiene il riconoscimento degli oggetti, la redaction, il ridisegno, il controllo delle lettere e gli avvisi. È tutta testabile senza finestre.
- `pdflex/designer.py`: la scheda Editor (`DesignTab`). Riusa `ui.render_page`, le miniature e lo stile di `editor.py` (Moduli). *(Nel codice `EditorTab` è già il nome della scheda Moduli: la nuova si chiama `DesignTab` per non confonderle.)*
- `pdflex/app.py`: la voce nella barra e il passaggio verso Moduli.
- Test: `tests/test_design.py` per il riconoscimento e le modifiche su PDF creati nei test (linea spostata, riquadro spostato con linee dentro, testo spostato, immagine, campo e pulsante che restano intatti, lettera mancante), più un test leggero della scheda.

## Lasciato al costruttore

- Le soglie per riconoscere "linea", "rettangolo" e "testo disegnato come forme", da tarare su Modelli.pdf e sulla busta paga.
- Come copiare il documento per Annulla, purché resti veloce.
- I margini esatti delle redaction: abbastanza larghi da prendere tutto l'oggetto, ma non da togliere il vicino. Se un vicino viene tolto, lo si ridisegna come al punto 2.
