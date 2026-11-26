# Moduli: modifica di più campi, incolla nelle tabelle, segni delle caselle, valute, avviso di salvataggio

## Perché

Su moduli lunghi (tabelle, elenchi di importi, molte caselle) oggi si sistema un campo alla volta. Incollare una riga di tabella richiede di trascinarla a mano ogni volta. L'utente vuole lavorare a gruppi, avere più scelte sull'aspetto di caselle e importi, e vedere chiaramente che il salvataggio è andato a buon fine.

## Obiettivi

1. **Modifica di più campi insieme.** Con più campi selezionati, sotto allinea/distribuisci compare lo stesso pannello delle proprietà di un campo singolo, ma solo con le voci che tutti i campi selezionati hanno. Dove i valori sono diversi la voce mostra "(diversi)" e resta così finché non la cambi; quello che cambi vale per tutti, con un solo Ctrl+Z.
   - **Tipo**: si cambia solo tra tipi simili, cioè testo, testo lungo e data. La voce Tipo compare anche per un campo singolo di questi tipi.
   - **Dimensioni**: larghezza e altezza in punti (nuove voci, anche per il campo singolo). L'angolo in alto a sinistra resta fermo.
   - **Formato**: formato, decimali, migliaia, valuta, schema di data, come per il campo singolo.
   - Il nome non compare con la selezione multipla: i nomi devono restare diversi.
2. **Incolla negli spazi simili (A + B).** Ctrl+V sulla stessa pagina:
   - **A, tabella.** Si cercano nella pagina le linee orizzontali che coprono almeno l'80% della larghezza dei campi copiati: linee disegnate e bordi superiore e inferiore dei rettangoli, quindi anche le celle disegnate come rettangoli. La riga attuale va dalla linea subito sopra i campi (a) alla prima linea sotto i campi (b). Si incolla scendendo di (b − a), quindi alla stessa posizione dentro la riga dopo. Le righe che hanno già campi si saltano, e ogni Ctrl+V scende di un'altra riga. Si va avanti finché c'è spazio nella pagina.
   - **B, ripiego senza tabella.** Se dopo un incolla l'utente sposta a mano i campi incollati, il Ctrl+V successivo ripete lo stesso spostamento (dalla posizione dei campi spostati). Senza spostamenti resta il comportamento di oggi: 10 punti in basso a destra.
   - Su un'altra pagina non cambia niente: stessa posizione.
3. **Segno delle caselle (checkbox).** Nuova voce "Segno": spunta, croce, punto, quadrato, rombo, stella (caratteri ZapfDingbats `3 8 l n u H`). Il segno si scrive in `/MK/CA` (lo usano i lettori che ridisegnano) e nell'aspetto `AP/N/Yes`, perché PyMuPDF disegna sempre la spunta: il glifo nello stream si sostituisce dopo la scrittura (verificato su PyMuPDF 2.1). Si rilegge da `/MK/CA` all'apertura. Il disegno nell'editor e l'anteprima mostrano il segno scelto.
4. **Importi con valuta.** "Importo in euro" diventa "Importo", con queste voci: **valuta** (€, $, £, CHF, nessuna), **posizione** (prima o dopo il numero), **decimali** (0–4, già presenti) e migliaia. La precisione è il numero di decimali: il lettore PDF arrotonda a quei decimali. Gli argomenti vanno in `AFNumber_Format(dec, sep, 0, 0, "<simbolo>", <prima>)`. Il formato si scrive `amount|dec|migliaia|simbolo|prima`, e i moduli già salvati (`amount|2|1`) restano euro prima del numero. In lettura si capiscono simbolo e posizione da qualunque PDF.
5. **Calcoli: media, minimo, massimo** (idea da PDFcraft). Accanto a "Somma di", una scelta "Somma / Media / Minimo / Massimo" (`AFSimple_Calculate("SUM"|"AVG"|"MIN"|"MAX")`), letta anche dai PDF esistenti.
6. **Avviso di salvataggio.** Un riquadro non bloccante in basso al centro della finestra: icona verde di conferma, "Salvato" in grassetto, nome del file sotto, ombra e angoli arrotondati nei colori del tema chiaro e scuro. Sparisce da solo dopo 4 secondi; resta mentre ci passi sopra con il mouse, e si chiude con un clic. Sostituisce i messaggi "Salvato in…" che oggi bloccano (Compila, Pagine, Compilazione in serie, Esporta per gestionale, con il testo esplicativo sotto il nome) e compare anche dove oggi non c'è nessun messaggio (salvataggio in Moduli e in Editor). Gli errori restano finestre bloccanti.

### Aggiunte dal confronto con PDFcraft / Acrobat ("Proprietà campo")

La documentazione di PDFcraft non elenca le impostazioni dei campi; la versione desktop (storytold/pdfcraft) copia la finestra "Proprietà campo" di Acrobat, quindi il confronto è con quella. L'utente ha scelto:

7. **Tabulazione.** Accanto a "Ordina per posizione" (per righe, c'è già) un "Ordina per colonne": per pagina, colonne da sinistra a destra (stessa colonna entro ~6 pt), poi dall'alto in basso. Un interruttore "Mostra ordine" disegna su ogni campo della pagina un piccolo numero tondo con la sua posizione nell'ordine (1, 2, 3… su tutto il modulo); i pulsanti di opzione dello stesso gruppo hanno lo stesso numero.
8. **Generale.** Per ogni campo (anche più campi insieme):
   - **Suggerimento**: testo che compare passando col mouse (`/TU`, `Widget.field_label`); vuoto = nessuno.
   - **Sola lettura**: flag `ReadOnly`; un totale calcolato resta sempre di sola lettura.
   - **Visibilità**: visibile (predefinito), nascosto (flag annotazione `Hidden`), solo in stampa (`NoView` + `Print`), solo a schermo (senza `Print`). Nell'editor un campo non visibile resta disegnato ma tratteggiato e trasparente, per poterlo ancora selezionare.
9. **Controlli.**
   - **Massimo caratteri** per testo e testo lungo (`/MaxLen` senza comb); con le caselle (comb) il numero di caselle resta il massimo, come oggi.
   - **Minimo / massimo** per numero, importo e percentuale: `AFRange_Validate(min?, min, max?, max)` nello script di validazione; uno dei due può restare vuoto.
   - **Prodotto** tra le operazioni di calcolo (`"PRD"`), insieme a somma, media, minimo e massimo del punto 5.

Tutto si rilegge dai PDF esistenti (PDFlex o altri programmi) e si conserva in blocchi, copie di sicurezza e copia/incolla.

## Fuori dallo scopo

- Modelli di modulo pronti (fattura, iscrizione…) visti su PDFcraft.
- Colori, spessore e stile del bordo e dello sfondo dei campi (proposto, non scelto).
- Ordine di calcolo, azioni JavaScript personalizzate, pulsanti invia/azzera.
- Segni diversi per i pulsanti di opzione (radio).
- Cambiare tipo tra famiglie diverse (es. casella ↔ testo).
- Incolla in tabella su pagine diverse da quella attuale.

## Da evitare

- Toccare le proprietà dei campi selezionati che l'utente non ha cambiato (una voce "(diversi)" non si scrive).
- Rompere i moduli salvati dalle versioni precedenti (formati `amount|2|1`, caselle senza `/MK/CA`).
- Un incolla in tabella che finisce sopra campi esistenti o fuori dalla pagina.

## Approcci considerati

- Selezione multipla: **pannello completo con valori comuni (scelto)** contro una finestra "Modifica selezionati…" con voci da spuntare, che richiede un passaggio in più.
- Incolla: **A (righe della tabella dalle linee del PDF) con B (ripeti lo spostamento manuale) come ripiego (scelto)**. A da solo non funziona sui PDF senza linee; B da solo obbliga a sistemare a mano la prima riga.
- Segno delle caselle: solo `/MK/CA` non basta, perché PyMuPDF ignora il carattere scelto e l'aspetto salvato è quello che vede la maggior parte dei lettori. Quindi `/MK/CA` più la sostituzione del glifo nell'aspetto.

## Scelte lasciate a chi costruisce

- Nome del nuovo attributo di `Field` per il segno e per l'operazione di calcolo, e come entrano in blocchi e copie di sicurezza.
- Tolleranze delle linee (≈1 pt) e soglia della larghezza coperta.
- Testi e traduzioni inglesi (i18n_en) delle nuove voci.
- Un test per ogni parte: formato importo andata e ritorno, segno andata e ritorno, incolla A e B su un PDF di prova con tabella, modifica multipla con voce "(diversi)".
