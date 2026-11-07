# Staff PC setup for publishing the Rosart website

Do this once on each PC that will publish rosart.com.au. It takes about 30 minutes. After that,
publishing is one request to Claude: "publish the website".

You need from the owner, privately (never by chat or email in plain text):

- access to the GitHub repo `rosart-web`;
- the **AWS deploy key** (an Access Key ID and a Secret Access Key);
- a Shopify staff account on the Rosart store.

## 1. Install the programs (Windows)

Open **PowerShell** and run these one at a time. Accept the prompts.

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Python.Python.3.13 -e
winget install --id ezwinports.make -e
winget install --id Amazon.AWSCLI -e
```

Close PowerShell and open it again (so it sees the new programs), then:

```powershell
npm install -g @shopify/cli@latest
irm https://claude.ai/install.ps1 | iex
```

The last line installs Claude Code; sign in with your Claude account the first time you run
`claude`. If `python --version` opens the Microsoft Store or says "Python was not found", open
Windows Settings > Apps > Advanced app settings > App execution aliases, turn off the two
`python` entries, and open a new terminal.

Use the `make` from `ezwinports.make`. MSYS2's `make` started from Git Bash hides your Windows
user folder from the AWS CLI, so the publish cannot find your AWS profile (`make doctor` says so
if it happens).

On a Mac: install Homebrew, then `brew install git node python make awscli`,
`npm install -g @shopify/cli@latest`, and Claude Code with
`curl -fsSL https://claude.ai/install.sh | bash`.

## 2. Tell git who you are

The publish records your name with each publish.

```sh
git config --global user.name "Your Name"
git config --global user.email "you@rosart.com.au"
```

## 3. Get the website folder

```sh
cd ~
git clone <the repo address the owner gives you> rosart-web
cd rosart-web
npm ci
```

## 4. Store the AWS deploy key

Type this yourself in the terminal. Never paste the key into Claude or any chat.

```sh
aws configure --profile rosart-deploy
```

Answer the four questions:

- **AWS Access Key ID**: from the owner
- **AWS Secret Access Key**: from the owner
- **Default region name**: `ap-southeast-2`
- **Default output format**: `json`

The key stays in your user folder on this PC, never in the website folder.

## 5. Sign in to Shopify (read-only)

Each command opens your browser: sign in with your Shopify staff account and approve.

```sh
shopify auth login
shopify store auth --store rosart-mhfmyuxq.myshopify.com --scopes read_content,read_products,read_online_store_navigation,read_files,read_themes,read_metaobjects,read_legal_policies
```

These are read-only permissions. Never sign in with any `write_` permission.

## 6. Check everything

In the `rosart-web` folder:

```sh
make doctor
```

Every line should say `PASS` (a `WARN` is fine). A `FAIL` line has a `fix:` line under it: do
that, then run `make doctor` again. If a fix says to contact the owner, do that.

## Publishing from now on

In the `rosart-web` folder, start Claude Code (`claude`) and say "publish the website". Claude
checks the PC, shows what changed in Shopify, asks you to confirm, publishes, and saves a record
of the publish on your PC. The owner uploads that record to GitHub; you never push.

Change content in Shopify only. If a check fails, Claude tells you what to fix in Shopify, or to
contact the owner.
