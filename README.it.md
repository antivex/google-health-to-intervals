# google-health-to-intervals (in breve)

> 🇬🇧 Full step-by-step guide in English: [README.md](README.md)

**I dati del tuo Fitbit — sonno, HRV, battito a riposo, peso — hanno smesso di
arrivare su intervals.icu il 2 novembre 2026?** Quel giorno Google ha spento la
vecchia Fitbit Web API: gli strumenti che la usavano ancora oggi ricevono
`HTTP 410 Gone`. Questo ponte usa al suo posto la nuova **Google Health API**.

Copia i dati di **Google Health** (Fitbit, Pixel Watch, Fitbit Air) su
**[intervals.icu](https://intervals.icu)**, quattro volte al giorno. Pensato per chi
non può usare le app Android (soprattutto chi ha l'**iPhone**). Gira **gratis sul tuo
account GitHub** (GitHub Actions): niente Raspberry Pi, niente server.

**Cosa copia:** sonno (`sleepSecs`), battito a riposo (`restingHR`), HRV (`hrv`),
peso (`weight`), SpO2 (`spO2`), passi (`steps`), VO2max (`vo2max`),
respirazione (`respiration`). Il punteggio del sonno non è disponibile nella
Google Health API. I valori vuoti non vengono mai inviati, quindi non cancellano
quello che hai già su intervals.icu.

> **Versione iniziale.** Lo strumento segue la documentazione di Google, ma alcuni
> nomi di campo non sono ancora stati verificati su dati reali. Se un valore ti
> sembra sbagliato, apri una issue.

## E lo strumento giusto per te?

Questo ponte legge la **Google Health API nel cloud**: i tuoi dati devono stare in
un account Google / Fitbit (app Fitbit, Fitbit Air, Pixel Watch).

- **Va bene** se hai una Fitbit o un Pixel Watch e non hai un telefono Android su
  cui far girare un'app, oppure se usi un iPhone.
- **Non va bene** se i tuoi dati stanno solo in **Health Connect sul telefono
  Android** (Mi Fitness, Zepp, Withings e simili): l'API nel cloud non li vede. In
  quel caso serve un'app Android, per esempio
  [daktak/intervals-icu-health-sync](https://github.com/daktak/intervals-icu-health-sync),
  che legge Health Connect direttamente (peso, massa grassa, sonno, passi, HRV,
  SpO2 e altro). Non c'entriamo niente con quell'app e non ci guadagniamo nulla.
- Se non va bene nessuna delle due, l'API di intervals.icu e il caricamento di un
  CSV wellness accettano questi campi anche a mano.

Un avviso valido per tutte le strade: alcuni campi wellness in intervals.icu sono
**nascosti per impostazione predefinita**. Se dopo una sincronizzazione un valore
sembra mancare, apri la finestra wellness, premi **Fields** e aggiungilo (per il
calendario: **Options -> Wellness**).

## Guarda cosa scrive, prima di installare niente (10 secondi)

Non serve un account Google, non serve una chiave e non serve l'installazione:
scarica questo repository, installa [uv](https://docs.astral.sh/uv/) e lancia

```
uv run ghti demo
```

Gira il codice vero della conversione su dati di esempio inventati, con la forma
descritta dalla documentazione dell'API. Non legge niente da Google e non manda
niente a nessuno:

```
Would send 3 day(s) to intervals.icu (2026-10-20 to 2026-10-22).
  2026-10-20: steps=8123, vo2max=47.3
  2026-10-21: sleepSecs=25200, restingHR=52, hrv=48.3, weight=72.9, spO2=96.4, steps=10456
  2026-10-22: sleepSecs=23400
```

Mostra anche come vengono trattati i casi che sbagliano tutti: il sonnellino non
viene contato, di due pesate nello stesso giorno vince la più recente, una notte
appartiene al giorno in cui ti svegli e un giorno con 0 passi viene lasciato
stare (non cancella quello che c'è su intervals.icu). Con `--json` vedi i record
esatti che intervals.icu riceverebbe.

Non sai cosa lanciare? `uv run ghti` senza comando scrive i quattro passi in
ordine e dice quali hanno bisogno della configurazione (in inglese).

## Come si installa

Prima di spendere i 20-30 minuti: questo ponte è **una** delle strade verso
intervals.icu, e su Android un'app Health Connect è di solito più semplice. Se non
sai quale strada va bene per il tuo telefono, la guida a pagamento qui sotto le
mette a confronto.

La guida completa, passo per passo, è nel [README in inglese](README.md). In sintesi:

1. **Use this template** → crea il repository come **privato**.
2. Crea un progetto su Google Cloud e abilita la **Google Health API**.
3. Schermata di consenso: **External**, stato **Testing**, aggiungi te stesso
   come utente di test e i tre permessi `googlehealth.*.readonly`.
4. Crea un client OAuth di tipo **Desktop app**.
5. Su intervals.icu: Settings → Developer Settings → chiave API e ID atleta.
6. Nel repository: Settings → Secrets and variables → Actions → aggiungi
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `INTERVALS_API_KEY`,
   `INTERVALS_ATHLETE_ID`. Consigliata la variabile `GHTI_TIMEZONE` = `Europe/Rome`.
7. Actions → **Re-authorize** → Run workflow (casella vuota) → apri il link nel
   riepilogo → approva → il browser mostra un errore (è normale) → copia
   l'indirizzo intero → di nuovo **Re-authorize**, incollalo in `redirect_url`.

8. **Controlla**: Actions → **Check setup** → Run workflow. Non scrive niente e
   nel riepilogo dice riga per riga cosa è pronto e cosa manca: i segreti, il
   fuso orario, la chiave intervals.icu (una sola lettura) e l'autorizzazione
   Google, con le ore che restano prima della scadenza. Rilancialo ogni volta
   che i dati non arrivano. Sul computer: `ghti check`.

## Farlo andare da solo ogni giorno

Sul computer, `ghti schedule` **scrive** l'operazione giornaliera già pronta per
il sistema che stai usando: le Operazioni pianificate su Windows, una riga di
crontab su macOS e Linux. Con i percorsi veri, un file di registro e il comando
per togliere tutto. **Non installa niente e non cambia nessuna impostazione**:
stampa i comandi, li leggi, e lanci quelli che vuoi tu.

```
uv run ghti schedule                      # per questo computer, alle 07:30
uv run ghti schedule --at 6:05 --days 7   # altra ora, più giorni per volta
```

Due cose le controlla invece di darle per buone: che il comando funzioni **senza
il PATH** del tuo terminale (l'operazione pianificata non ce l'ha, ed è il
motivo tipico per cui va a mano e di notte no) e che i tre segreti stiano in
`config.env` e non solo nelle variabili d'ambiente — da quelle un'operazione
pianificata non li vede e fallirebbe ogni notte. Se è così, te lo dice prima
di tutto il resto.

Ogni giro copia gli ultimi 3 giorni: una notte col portatile spento non costa
niente, il giro dopo recupera.

### Ma è andato davvero? `ghti status`

Ogni sincronizzazione vera scrive una riga in un diario accanto al token
(`runs.jsonl` nella cartella di configurazione; `GHTI_JOURNAL_FILE` lo sposta).
`ghti status` legge l'ultima riga, così controlli un'operazione che gira mentre
dormi senza aprire nessun registro:

```
uv run ghti status
Last sync: 2026-11-05 06:30 UTC (today), 3 day(s) sent.
Google authorization from 2026-11-04, expires on 2026-11-11 06:30 UTC (in 144 h).
```

Se l'operazione pianificata smette di partire — una cartella rinominata,
l'operazione cancellata, il portatile che non si sveglia — non si rompe niente
con un errore: i dati smettono solo di arrivare. Per questo dopo due giorni di
silenzio `status` lo dice:

```
Last sync: 2026-11-01 06:30 UTC (4 days ago), 3 day(s) sent.
  Nothing has run for 4 days. If it should run on its own, the daily task is
  not firing: check it, or print it again with `ghti schedule`.
```

Viene scritto anche un giro fallito, con il messaggio che avresti letto; un giro
con `--dry-run` non viene scritto. Nel diario ci sono solo date, conteggi, nomi
delle misure e messaggi d'errore — mai i tuoi valori e mai un segreto — quindi
puoi allegarlo a una segnalazione così com'è.

## Ogni settimana

Google fa scadere l'autorizzazione delle app in "Testing" dopo 7 giorni. Il
sesto giorno il workflow **Reminder** diventa rosso apposta e GitHub ti manda
un'email con il link: ripeti il punto 7 (un minuto). Se lo salti, la sync si
ferma ma i giorni persi vengono recuperati dopo la nuova autorizzazione.

## Privacy

I dati vanno solo da Google a intervals.icu. Nessun server nostro, nessun dato
di salute salvato nel repository. Il token è cifrato in `state/token.enc` con una
chiave derivata dai tuoi segreti.

## Supporto e guida a pagamento

Il ponte è gratis e resta gratis. Se ti fa risparmiare tempo, puoi offrire un
caffè su https://ko-fi.com/antivex.

C'è anche una guida PDF a pagamento (in inglese),
[Keep Your Fitbit Data After the Google Health Switch](https://antivex.gumroad.com/l/fitbit-google-health-intervals),
per chi non sa quale strada scegliere: mette a confronto passo per passo Health
Connect, Health Sync, Apple Health, il caricamento CSV, l'API di intervals.icu e
questo ponte e spiega come recuperare i giorni persi dal 2 novembre. Non serve
per usare il ponte: i messaggi d'errore del ponte, con la soluzione, sono gratis
nella [lista in inglese](README.md#error-messages-one-by-one).

Licenza MIT. Non siamo collegati a Google, Fitbit o intervals.icu.
