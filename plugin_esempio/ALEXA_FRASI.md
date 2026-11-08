# Alexa — frasi e Routine

Skill: **centrale termica** (italiano, Italia). Plugin `alexa.py` versione 1.4.0.

## Come si usa

- A voce, nella forma lunga: «Alexa, chiedi a centrale termica *frase*».
- Con una Routine (consigliato): nell'app Alexa → Routine → azione **Personalizzata** → testo `chiedi a centrale termica <frase>`.
  La frase che fa partire la Routine (quello che dici tu) la scegli liberamente nell'app: è indipendente da quella dell'azione.

## Domande e comandi

| Cosa | Frasi che la skill capisce | Azione da mettere nella Routine | Risposta di esempio |
|---|---|---|---|
| Temperatura | temperatura · la temperatura · che temperatura c'è · che temperatura c'è in casa · quanti gradi ci sono · quanti gradi ci sono in casa · temperatura in casa | `chiedi a centrale termica temperatura` | «In casa ci sono 20,7 gradi e il 52 per cento di umidità. Lo Smarther è impostato a 21.» |
| Umidità | umidità · l'umidità · che umidità c'è · che umidità c'è in casa · quanta umidità c'è · umidità in casa | `chiedi a centrale termica umidità` | «In casa c'è il 52 per cento di umidità e ci sono 20,7 gradi.» |
| Temperatura fuori | temperatura fuori · che temperatura c'è fuori · quanti gradi ci sono fuori · temperatura esterna · all'esterno · com'è fuori · fuori | `chiedi a centrale termica temperatura fuori` | «Fuori ci sono 8,8 gradi e l'87 per cento di umidità.» (senza stazione meteo: solo i gradi) |
| Umidità fuori | umidità fuori · che umidità c'è fuori · quanta umidità c'è fuori · umidità esterna | `chiedi a centrale termica umidità fuori` | «Fuori c'è l'87 per cento di umidità e ci sono 8,8 gradi.» · senza dato: «Non ho l'umidità di fuori. Ci sono 8,8 gradi.» |
| Riscaldamento acceso? | il riscaldamento è acceso · riscaldamento acceso · è acceso il riscaldamento · sta scaldando | `chiedi a centrale termica riscaldamento acceso` | Acceso: «Sì, il riscaldamento è acceso. In casa ci sono 20,7 gradi e lo Smarther è impostato a 21.»<br>Fermo: «No, in questo momento il riscaldamento è spento. In casa ci sono… Dovrebbe ripartire verso le 17 e 15.»<br>Acqua calda: «No, ora la pompa sta scaldando l'acqua calda.»<br>Sbrinamento: «No, ora la pompa sta sbrinando.»<br>Spento dal pannello: «No, il riscaldamento è disattivato. Lo Smarther è impostato a 21.» |
| Ripartenza | a che ora partirà il riscaldamento · quando riparte il riscaldamento · quando riparte · a che ora riparte il riscaldamento · ripartenza | `chiedi a centrale termica quando riparte` | «Dovrebbe ripartire verso le 17 e 15.» · «È già acceso.» · «È disattivato.» · «Non riesco a stimare quando ripartirà: non ho abbastanza dati.» |
| Pompa di calore | cosa sta facendo la pompa di calore · pompa di calore · stato della pompa di calore · cosa fa la pompa di calore · come va la pompa di calore | `chiedi a centrale termica pompa di calore` | «La pompa di calore sta scaldando la casa. La mandata è a 31 gradi. Sta assorbendo 850 watt. Fuori ci sono 8,4 gradi e l'87 per cento di umidità.» |
| Acqua calda | a che temperatura è l'acqua calda · acqua calda · temperatura dell'acqua calda · quanto è calda l'acqua | `chiedi a centrale termica acqua calda` | «L'acqua calda è a 47 gradi. L'obiettivo è 50. La pompa la sta scaldando adesso.» |
| Mandata | che mandata ha il riscaldamento · mandata · mandata del riscaldamento · a che temperatura è la mandata | `chiedi a centrale termica mandata` | «La mandata è a 31,2 gradi. Il ritorno è a 28.» |
| **Accendi Smarther** | accendi il termostato · accendi termostato · accendi lo smarther · attiva il termostato | `chiedi a centrale termica accendi il termostato` | «Fatto, ho acceso il termostato.» |
| **Spegni Smarther** | spegni il termostato · spegni termostato · spegni lo smarther · disattiva il termostato | `chiedi a centrale termica spegni il termostato` | «Fatto, ho spento il termostato.» |

Sempre disponibili: *aiuto* (elenca cosa si può chiedere), *stop / annulla* («D'accordo.»). Aprendo la skill senza domanda risponde «Dimmi pure…».

## Regole delle risposte

- Se l'ultimo dato ha più di 30 minuti, la risposta aggiunge «Attenzione: l'ultimo dato risale a N minuti fa.»
- Se mancano i dati della pompa: «Non ho dati recenti dalla pompa di calore.»
- L'ora di ripartenza si dice «verso le 17 e 15», «verso l'una e mezza», «verso mezzanotte»; se è il giorno dopo, «…di domani».
- A voce si può solo **accendere/spegnere lo Smarther**. La pompa di calore è in sola lettura.

## Note

- Funziona nell'Alexa classica. Con **Alexa+** i nomi di invocazione vengono intercettati dalla "Casa intelligente" o confusi con altre skill e l'esito è instabile: non risolto (vedi changelog); consigliata l'Alexa classica.
- Per imparare le frasi: usa quelle della tua **Routine** (tabella «Le Routine create» qui sotto); la forma con la skill serve solo se la Routine non c'è.
- Se aggiungi frasi al modello vocale (`alexa_modello_it.json`): Console Amazon → Build → JSON Editor → incolla → Save → Build.

## Le Routine create nell'app Alexa (04/11/2026, dagli screenshot)

Frase che dici (il «Quando» della Routine) → azione Personalizzata `chiedi a centrale termica <frase della tabella sopra>`. Sono 11, una per domanda, nessuna ripetuta:

| # | Frase che dici | Cosa chiede |
|---|---|---|
| 1 | che temperatura c'è in casa | temperatura |
| 2 | che umidità c'è in casa | umidità |
| 3 | quanti gradi ci sono fuori | temperatura fuori |
| 4 | che umidità c'è fuori | umidità fuori |
| 5 | il riscaldamento è acceso | riscaldamento acceso |
| 6 | a che ora partirà il riscaldamento | quando riparte |
| 7 | cosa sta facendo la pompa di calore | pompa di calore |
| 8 | a che temperatura è l'acqua calda | acqua calda |
| 9 | che mandata ha il riscaldamento | mandata |
| 10 | Accendi il termostato | accendi il termostato |
| 11 | spegni il termostato | spegni il termostato |

Con Alexa+ (prova del 04/11): la skill risponde bene ma Alexa+ a volte sceglie un'altra skill (riuscito al 5° tentativo); «apri centrale termica» non parte, «apri la skill centrale termica» si; il saluto «Benvenuto in <nome pubblico della skill>» lo dice Alexa. Frasi-variante da aggiungere: vedi PIANO.md.

