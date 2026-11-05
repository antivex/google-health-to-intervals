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
