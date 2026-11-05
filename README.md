# google-health-to-intervals

> 🇮🇹 Versione breve in italiano: [README.it.md](README.it.md)

**Did your Fitbit sleep, HRV, resting heart rate or weight stop arriving in intervals.icu on 2 November 2026?**
Google turned off the old Fitbit Web API that day: tools that still used it now get `HTTP 410 Gone`.
This bridge uses the new **Google Health API** instead.

Copy your **Google Health** data (Fitbit, Pixel Watch, Fitbit Air) to
**[intervals.icu](https://intervals.icu)** automatically, a few times a day.

It is made for people who cannot use the Android apps that do this through
Health Connect, especially **iPhone users**. It runs **for free on your own
GitHub account** (GitHub Actions): no Raspberry Pi, no server, no app to install.

### Is this the right tool for you?

This bridge reads the **Google Health API in the cloud**, so your data has to be
in a Google / Fitbit account (Fitbit app, Fitbit Air, Pixel Watch).

- **It fits** if you have a Fitbit or Pixel Watch and no Android phone to run an
  app on, or you are on iPhone.
- **It does not fit** if your data only lives in **Health Connect on an Android
  phone** (Mi Fitness, Zepp, Withings and similar), because the cloud API never
  sees it. In that case an Android app is the right route: for example
  [daktak/intervals-icu-health-sync](https://github.com/daktak/intervals-icu-health-sync)
  reads Health Connect directly (weight, body fat, sleep, steps, HRV, SpO2 and
  more) and writes to the intervals.icu wellness endpoint. We are not involved
  with it, and we get nothing if you use it.
- If neither fits, the intervals.icu API and a wellness CSV upload both accept
  these fields by hand.

A note for either route: some wellness fields are **hidden by default** in
intervals.icu. If a value looks missing after a sync, open the wellness dialog,
click **Fields** and add it (for the calendar: **Options -> Wellness**).

| Google Health | intervals.icu wellness field |
|---|---|
| Sleep duration (time asleep, naps excluded) | `sleepSecs` |
| Resting heart rate | `restingHR` |
| Heart rate variability (nightly rMSSD) | `hrv` |
| Weight (last weigh-in of the day) | `weight` |
| Body fat % (last reading of the day) | `bodyFat` |
| SpO2 (daily average) | `spO2` |
| Steps | `steps` |
| Floors climbed | `floorsClimbed` |
| VO2 max (cardio fitness) | `vo2max` |
| Breathing rate | `respiration` |

Body fat and floors climbed only arrive if a device of yours measures them (a
smart scale, an altimeter). The Google Health API does not provide the Fitbit
*sleep score*, so `sleepScore` stays empty. Empty values are never sent, so they never erase what
you already have in intervals.icu.

> **Status: early version.** The Google Health API replaced the Fitbit Web API
> in 2026. This tool follows Google's documentation, but some field names have
> not been checked against real data yet. If a value looks wrong, please open an
> issue.

## How it works

```
Google Health API  --->  your private GitHub repository (GitHub Actions)  --->  intervals.icu
```

- You create your **own** Google Cloud project, in "Testing" mode. Google does
  not need to review it because only you use it.
- The price of "Testing" mode: **Google asks you to approve access again every
  7 days.** That comes from the publishing status, not from the app being
  unverified ([Google's OAuth docs](https://developers.google.com/identity/protocols/oauth2):
  a consent screen with an external user type and a publishing status of
  *Testing* gets "a refresh token expiring in 7 days"). This tool turns the
  renewal into a one-minute step, and GitHub emails you the day before.
- Your data goes straight from Google to intervals.icu. There is no server of ours.

## See what it writes, before setting anything up (10 seconds)

You do not need a Google account, a key or the setup below to see what lands in
intervals.icu. Download this repository, install [uv](https://docs.astral.sh/uv/)
and run:

```
uv run ghti demo
```

It runs the real mapping code on example data that is invented, shaped like the
API reference. Nothing is read from Google and nothing is sent anywhere:

```
Example data, invented (no Google account, no network, nothing is sent).
It shows what a real sync would write to intervals.icu, and how the awkward
cases are handled: a nap is ignored, the later weigh-in of a day wins, a night
belongs to the day you wake up, and a day with 0 steps is left alone.

Would send 3 day(s) to intervals.icu (2026-10-20 to 2026-10-22).
  2026-10-20: steps=8123, floorsClimbed=12, vo2max=47.3
  2026-10-21: sleepSecs=25200, restingHR=52, hrv=48.3, weight=72.9, bodyFat=18.9, spO2=96.4, steps=10456, floorsClimbed=7
  2026-10-22: sleepSecs=23400
```

`uv run ghti demo --json` also prints the exact records intervals.icu would
receive. The fields are the wellness fields of intervals.icu: `sleepSecs`,
`restingHR`, `hrv`, `weight`, `bodyFat`, `spO2`, `steps`, `floorsClimbed`,
`vo2max`, `respiration`. A field
with no data is never sent, so it cannot erase what intervals.icu already has.

Lost? `uv run ghti` with no command prints the four steps in order, and says
which of them need the setup below.

## Setup (about 20-30 minutes, once)

You need: the Google account that has your Fitbit / Pixel Watch data, an
intervals.icu account, and a free GitHub account.

Before you spend those 20-30 minutes: this bridge is **one** of several routes
into intervals.icu, and on Android a Health Connect app is usually simpler. If
you are not sure which route fits your phone, the paid guide
[Keep Your Fitbit Data After the Google Health Switch](https://antivex.gumroad.com/l/fitbit-google-health-intervals)
compares them side by side (including what each one does *not* carry, such as
HRV through Apple Health) and shows how to fill the days missing since
2 November. The bridge itself is free and stays free.

### 1. Create your copy of this repository (PRIVATE)

1. Sign in to GitHub and open this repository's page.
2. Click the green **Use this template** button, then **Create a new repository**.
3. Choose a name (for example `my-health-sync`).
4. Select **Private**. This matters: keep your copy private.
5. Click **Create repository**.

### 2. Create a Google Cloud project

1. Open [console.cloud.google.com](https://console.cloud.google.com/) with the
   Google account that has your health data. Accept the terms if asked.
2. At the top, click the project selector, then **New project**. Name it
   `health-sync` and click **Create**. Make sure the new project is selected.

This tool uses only the Google Health API; it does not need any paid Google Cloud product.

### 3. Enable the Google Health API

1. Open [the Google Health API page](https://console.cloud.google.com/apis/library/health.googleapis.com)
   (or search for "Google Health API" in **APIs & Services > Library**).
2. Click **Enable**.

### 4. Set up the consent screen ("Testing", with yourself as test user)

1. Open **Google Auth Platform** (search for "OAuth consent screen" at the top
   of the console) and click **Get started**.
2. **App information**: app name `health-sync`, support email = your email.
3. **Audience**: choose **External**.
4. **Contact information**: your email. Accept the policy and click **Create**.
5. Go to **Audience**. Leave the publishing status on **Testing**.
   Under **Test users** click **Add users**, type your own Google email
   (the one with the health data) and click **Save**.
6. Go to **Data Access** and click **Add or remove scopes**. Type `googlehealth`
   in the filter and tick these three, then click **Update** and **Save**:
   - `.../auth/googlehealth.sleep.readonly`
   - `.../auth/googlehealth.health_metrics_and_measurements.readonly`
   - `.../auth/googlehealth.activity_and_fitness.readonly`

### 5. Create the OAuth client ("Desktop app")

1. In **Google Auth Platform** open **Clients** and click **Create client**.
2. **Application type: Desktop app**. Name: `health-sync`. Click **Create**.
3. Copy the **Client ID** and the **Client secret** somewhere safe for the next
   step. (You can see them again later by opening the client.)

### 6. Get your intervals.icu API key

1. In intervals.icu open **Settings**, scroll to **Developer Settings**.
2. Click to create an **API key** and copy it.
3. Note your **Athlete ID** shown in the same place (it looks like `i123456`).

### 7. Add the 4 secrets to your GitHub repository

In your new repository: **Settings > Secrets and variables > Actions >
New repository secret**. Add these four, one at a time:

| Name | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | the Client ID from step 5 |
| `GOOGLE_CLIENT_SECRET` | the Client secret from step 5 |
| `INTERVALS_API_KEY` | the API key from step 6 |
| `INTERVALS_ATHLETE_ID` | your athlete ID from step 6 (e.g. `i123456`) |

Recommended: in the **Variables** tab (same page) add `GHTI_TIMEZONE` with your
time zone, for example `Europe/Rome` or `America/New_York`. Without it "today"
is computed in UTC.

### 8. Authorize Google (first time)

1. Open the **Actions** tab of your repository. If GitHub asks, click
   **I understand my workflows, go ahead and enable them**.
2. Click **Re-authorize** on the left, then **Run workflow**, leave the box
   empty and click the green **Run workflow** button.
3. Open the run that appears (refresh the page after a few seconds). In its
   **Summary** you will find the link **Authorize Google Health**. Open it.
4. Choose your Google account. Google says *"Google hasn't verified this app"*:
   it is your own app, so click **Continue**. Tick **all** the boxes and click
   **Continue**.
5. Your browser ends on an error page such as *"This site can't be reached"*.
   **That is expected.** Copy the **whole address** from the address bar. It
   starts with `http://127.0.0.1:8765/?state=`.
6. Within a few minutes, go back to **Actions > Re-authorize > Run workflow**,
   paste the address into **redirect_url** and click **Run workflow**.

The run saves the authorization (encrypted, in `state/token.enc`) and
immediately copies the last 14 days.

### 9. Check that it works

**One click, any time: Actions > Check setup > Run workflow.** It writes nothing
and its Summary lists, line by line, what is ready and what is not: the
secrets, the time zone, your intervals.icu key (one read-only request) and the
Google authorization, with the hours left before it expires. Use it after the
setup, and again whenever data stops arriving. On a computer: `ghti check`.

```
Setup check
  ok    Secrets: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and INTERVALS_API_KEY are set.
  ok    Time zone: Days are cut at midnight in Europe/Rome.
  ok    intervals.icu: The key works. Data will be written to athlete i123456.
  ok    Google authorization: Google accepted it. It expires on 2026-11-12 09:00 UTC (in 144 h).

Everything is ready: the next run will copy your data.
```

- The Re-authorize run should be green, and its Summary shows a table of the
  days copied.
- Open intervals.icu: the wellness values appear on the calendar and in the
  Fitness / Wellness charts.
- From now on the **Sync** workflow runs four times a day by itself. You can
  also start it by hand: **Actions > Sync > Run workflow**.

To copy older history: **Actions > Sync > Run workflow** and fill **from** with
the first day you want (for example `2025-01-01`).

## Weekly re-authorization

Google expires the authorization of "Testing" apps 7 days after you approve it.

- **Day 6**: the **Reminder** workflow turns red on purpose, so GitHub sends you
  an email. Its Summary says *"the Google authorization expires tomorrow"* and
  contains the link.
- Renewing takes a minute: open the link, approve, copy the address, run
  **Re-authorize** with it (steps 8.3 to 8.6).
- If you miss it, **Sync** fails with a clear message and the same link, and
  GitHub emails you again. Nothing is lost: after you re-authorize, the missed
  days (up to 14 by default) are copied automatically.
- The expiry date comes from Google (field `refresh_token_expires_in`). When a
  token arrives **without** a date, the 7 days are only this tool's guess: once
  the guess runs out, **Reminder** asks Google directly and, if the authorization
  still works, it says so and goes quiet for another 7 days. A guess never turns
  into a red run that asks you to re-authorize for nothing.

Don't want the red reminder run? Add the repository variable
`GHTI_REMINDER_EMAIL` = `false`. The reminder then only appears in the run Summary.

## Privacy

- Your health data travels **only from Google to intervals.icu**, inside
  GitHub's runner that works for you. There is **no server of ours** and no
  analytics.
- No health data is stored in your repository. The run Summary shows which
  fields were copied for each day, not the values.
- The Google authorization is stored encrypted (AES-256-GCM) in
  `state/token.enc`, with a key derived from your `GOOGLE_CLIENT_SECRET` and
  `INTERVALS_API_KEY` secrets. Your secrets stay in GitHub's secret store.
- Access is read-only: the tool cannot change or delete anything in Google Health.
- You can revoke access at any time at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Limits

- **Re-authorization every 7 days**, because the Google project's publishing
  status is "Testing": Google then issues a refresh token that expires in 7 days
  ([source](https://developers.google.com/identity/protocols/oauth2)). Google
  says so in the token response, and that is the date `ghti status` shows. If
  Google ever stops sending a date, the tool treats its own 7 days as a **guess**:
  it asks Google whether the authorization still works before stopping a sync or
  asking you to click anything.
- A "Testing" project accepts at most **100 test users**. That is why every user
  creates their own project.
- It depends on the **Google Health API**: if Google changes the API or its
  rules, the tool may need an update.
- Data is copied 4 times a day, not in real time. Today's steps are completed by
  the later runs.
- GitHub Actions is free for private repositories within the monthly minutes of
  the free plan; this tool uses a small part of them (about 5 short runs a day).
- intervals.icu allows 5,000 API requests a day; a normal sync uses one.

## FAQ

**Do I need Fitbit Premium?**
The tool copies whatever the Google Health API returns for your account. Which
metrics a device or subscription provides depends on Google, not on this tool.

**Does it work with an iPhone?**
Yes. Nothing runs on your phone: the data is read from Google's cloud.

**Why does the browser show an error during authorization?**
The OAuth client is a "Desktop app", so Google sends you back to
`127.0.0.1` (your own computer), where nothing is listening. The code you need
is in the address. This replaces Google's old copy-paste flow, which was shut down.

**Can I run it on my computer instead of GitHub?**
Yes, see *Run on your computer* below.

**I changed my intervals.icu API key or Google client secret. Now what?**
Update the secret on GitHub and authorize again: the stored token is encrypted
with those secrets.

**Some values are missing.**
Check that you ticked all the boxes on Google's consent screen (the Summary
lists anything not authorized), and that your device records that value.
If the Summary shows a "Problems" section, please open an issue with that text
(it contains no health data).

**Nothing arrives and no run is red. Where do I look?**
Run **Actions > Check setup**: it says which of the four parts (secrets, time
zone, intervals.icu key, Google authorization) is not ready, without writing
anything. Then check that the values are not hidden in intervals.icu (*Fields*
in the wellness dialog).

**Can I make my copy public?**
Please don't. The token is encrypted, but a private repository is the safe choice.

## Error messages, one by one

Not sure which part is broken? Run **Actions > Check setup** (or `ghti check`)
first: it tests the secrets, the intervals.icu key and the Google authorization
one by one and writes nothing.

Every message the bridge can print, with the fix. Copy the first line of the
error and look for it here. If yours is not in this list, please
[open an issue](https://github.com/antivex/google-health-to-intervals/issues/new)
with the text: the error text carries no health data, and the fix then helps
everybody.

### Setup

| Message | What to do |
|---|---|
| `Missing setting(s): ...` | Add the four secrets on GitHub under *Settings > Secrets and variables > Actions*. The names are case sensitive: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `INTERVALS_API_KEY`, `INTERVALS_ATHLETE_ID`. On a computer they go in environment variables or in `config.env`. |
| `GHTI_TIMEZONE=... is not a valid time zone` | Use a time zone name, not an offset: `Europe/Rome`, `America/New_York`. |

### Authorization, the first time

| Message | What to do |
|---|---|
| `Paste the address from the browser: ghti auth-finish "http://127.0.0.1:8765/?state=..."` | You ran `auth-finish` with no address. Put the address from the browser between quotes, right after the command. In the workflow it goes in the *Address from the browser* field. |
| `That does not look like the address from the browser` | Copy the **whole** address bar after you click Allow, starting with `http://127.0.0.1:8765/?state=`, and keep it in quotes. The browser page showing an error is normal (see the FAQ above). |
| `Unexpected address host '...': expected 127.0.0.1` | You pasted a different link (often the consent link instead of the one you were sent back to). Use the address you landed on after allowing. |
| `The address has no 'code' or 'state'` | The address was cut. Copy it again from the address bar, not from the page text. |
| `Google returned 'access_denied': access was not granted` | Run `auth-url` again and click *Continue* / *Allow*, ticking **every** box. |
| `This address belongs to a link created with different secrets` | You changed `GOOGLE_CLIENT_SECRET` or `INTERVALS_API_KEY` after creating the link. Create a new link with `auth-url`. |
| `Google refused the code (invalid_grant: ...)` | The code in the address works once and only for a few minutes. Create a new link with `auth-url` and finish straight away. |
| `Google did not return a refresh token` | Google only sends it the first time. Remove the app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions), then authorize again. |
| `Google rejected the OAuth client (invalid_client)` | Check `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` against the client in Google Cloud, and that the client type is *Desktop app*. |

### The weekly re-authorization

| Message | What to do |
|---|---|
| `No Google authorization found (...)` | Nobody has authorized yet, or `state/token.enc` was deleted. Run *Actions > Re-authorize* (or `auth-url` plus `auth-finish`). |
| `Cannot decrypt ...: GOOGLE_CLIENT_SECRET or INTERVALS_API_KEY changed since the last authorization` | The token is encrypted with those two secrets. After changing either one, authorize again. |
| `The Google authorization expired on ...` or `has expired or was revoked` | Normal: a project in *Testing* mode expires every 7 days. Authorize again. `ghti status` says when it expires. |
| `Could not refresh the Google token (HTTP ...)` | Google refused the renewal for another reason (often a network problem or a Google outage). Run *Check setup* to see which part fails; if it keeps failing, authorize again. |
| `not a ghti token file` or `wrong key or damaged file` | Delete `state/token.enc` and authorize again. |

### Google Health API

| Message | What to do |
|---|---|
| `Google Health API error 403 ...: ... has not been used / is disabled` | Enable the **Google Health API** in your Google Cloud project. |
| `Google Health API error 403 ...` (any other) | That permission was not granted. Authorize again and tick every box on the consent screen. |
| `Google Health API error 401 ...` | The access token was refused. Authorize again. |
| `Google Health API error 429 ...` | Google's rate limit. Nothing to fix: the next run tries again. |
| `Google Health API error 500 / 503 ...` (or any other code) | A problem on Google's side. Nothing to fix: the next run tries again. |
| `Nothing could be read from Google Health: - <metric>: ...` | Every metric failed, so nothing was sent. The lines below the message say why, one metric at a time: read the first one and look it up here. Most often the authorization has to be done again. |

### intervals.icu

| Message | What to do |
|---|---|
| `intervals.icu refused the request (HTTP 401)` or `(HTTP 403)` | Check `INTERVALS_API_KEY` (Settings > Developer on intervals.icu) and `INTERVALS_ATHLETE_ID`: `0` means the key's owner, otherwise it looks like `i123456`. |
| `intervals.icu athlete '...' not found (HTTP 404)` | Wrong `INTERVALS_ATHLETE_ID`. |
| `intervals.icu rate limit reached (HTTP 429)` | Nothing to fix: the next run tries again. |
| `Too many intervals.icu requests in one run` | A backfill that is too long. Split it with `--from` / `--to` into shorter ranges. |
| `intervals.icu error HTTP 500: ...` (or any other code) | A problem on the intervals.icu side. Nothing to fix: the next run tries again. |

### Command arguments

| Message | What to do |
|---|---|
| `--days must be between 1 and 3650` | Use a number in that range; for older history use `backfill --from`. |
| `--from (...) is after --to (...)` | Swap the two dates. |
| `The range is longer than 3650 days` | Split the backfill into shorter ranges. |
| `'...' is not a date in the form YYYY-MM-DD` | Write dates as `2025-01-31`. |
| `--at wants a time like 07:30` | `ghti schedule --at` takes a 24-hour time with a colon: `07:30`, `6:05`, `23:00`. |
| `--days wants at least 1.` | `ghti schedule --days` is how many days each run copies: 1 or more, 3 by default. |

## Run on your computer

Install [uv](https://docs.astral.sh/uv/), download this repository, then put the
same four values either in environment variables or in a file named
`config.env` in your configuration folder (`%APPDATA%\ghti` on Windows,
`~/Library/Application Support/ghti` on macOS, `~/.config/ghti` on Linux):

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
INTERVALS_API_KEY=...
INTERVALS_ATHLETE_ID=i123456
GHTI_TIMEZONE=Europe/Rome
```

Commands:

```
uv run ghti demo                          # example output, no setup and no network
uv run ghti check                         # is the setup ready? (writes nothing)
uv run ghti auth-url                      # print the consent link
uv run ghti auth-finish "<address>"       # save the authorization
uv run ghti sync                          # copy the last 3 days (--days N to change)
uv run ghti sync --dry-run --show-values  # look without writing
uv run ghti backfill --from 2025-01-01    # copy older history
uv run ghti export-csv -o wellness.csv    # last 30 days as CSV (--from/--to)
uv run ghti status                        # the last run, and when the authorization expires
uv run ghti schedule                      # the daily task, written out (installs nothing)
```

On a computer the encrypted token is saved in the same configuration folder.

### Let it run every day on its own

`ghti schedule` prints the daily task for the computer you are on — Windows Task
Scheduler, or a one-line crontab entry on macOS and Linux — with the real paths
already filled in, a log file, and the command to remove it again. It **installs
nothing and changes no setting**: it prints the commands so you can read them
first, and you run the ones you want.

```
uv run ghti schedule                      # for this computer, at 07:30
uv run ghti schedule --at 6:05 --days 7   # another time, more days per run
uv run ghti schedule --os linux           # see the version for another system
```

Two things it checks instead of assuming:

- **the way `ghti` is called.** A scheduled task does not get the PATH of your
  terminal, so the printed command uses absolute paths. The bare word `ghti` is
  exactly what works by hand and fails at 07:30.
- **where your secrets are.** From `config.env` any task finds them; from
  environment variables exported in a terminal it does not, and the run would
  fail every night. In that case `ghti schedule` says so before anything else.

A run copies the last 3 days, so a night the laptop was asleep costs nothing:
the next run picks the missed days up. Repeating a day is harmless — the same
values overwrite the same day.

### Did it actually run? `ghti status`

Every real sync writes one line in a small diary next to the token
(`runs.jsonl` in the configuration folder; `GHTI_JOURNAL_FILE` moves it).
`ghti status` reads the last line, so you can check a task that runs while you
are asleep without opening a log:

```
uv run ghti status
Last sync: 2026-11-05 06:30 UTC (today), 3 day(s) sent.
Google authorization from 2026-11-04, expires on 2026-11-11 06:30 UTC (in 144 h).
```

If the daily task stops firing — a renamed folder, a removed task, a laptop that
never wakes up — nothing fails loudly: data just stops arriving. So after two
quiet days `status` says it:

```
Last sync: 2026-11-01 06:30 UTC (4 days ago), 3 day(s) sent.
  Nothing has run for 4 days. If it should run on its own, the daily task is
  not firing: check it, or print it again with `ghti schedule`.
```

A run that failed is written too, with the message you would have seen, and a
run with `--dry-run` is not written at all. The diary holds dates, counts,
metric names and error messages only — never your values and never a secret —
so you can attach it to a bug report as it is.

## Development

```
uv run pytest -q
```

The tests are offline: every HTTP call is faked. The mapping between Google's
fields and intervals.icu lives in one file, `src/ghti/mapping.py`.

One more command checks that file against Google itself, with no account and no
credentials:

```
uv run python tools/check_spec.py
```

It downloads the Google Health API's public discovery document — the
machine-readable spec its own client libraries are generated from — and asserts
that every data type, JSON field, filter path, response key, page cap and scope
this bridge uses is really there. On 2026-11-05 all 32 checks passed against
revision `20261105`. What a spec cannot settle is what a number *means* (is
Google's nightly HRV the rMSSD intervals.icu expects? does a steps total of 0
mean "did not walk" or "watch not worn"?): those assumptions, and only those,
are still marked `TO VERIFY WITH REAL DATA`.

## Support / paid guide

The bridge is free and stays free. If it saves you time, you can leave a tip at https://ko-fi.com/antivex.

There is also a paid PDF guide,
[Keep Your Fitbit Data After the Google Health Switch](https://antivex.gumroad.com/l/fitbit-google-health-intervals),
for people who are not sure which route fits them: it compares Health Connect, Health Sync, Apple Health,
CSV upload, the intervals.icu API and this bridge step by step, and shows how to fill the gap since
2 November. You don't need it to use the bridge: this bridge's error messages are free, above in
[Error messages, one by one](#error-messages-one-by-one).

## License

MIT, see [LICENSE](LICENSE). Not affiliated with Google, Fitbit or intervals.icu.
