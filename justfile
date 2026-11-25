# Voltrieve shortcuts. Run `just` to list them.

_default:
    @just --list

# Chat with a backend: loop, prompt or hosted
chat backend="loop" *flags="":
    uv run --project clients/cli voltrieve chat --backend {{backend}} --az-token {{flags}}

# Chat and watch every step
trace backend="loop":
    @just chat {{backend}} --trace

# Ask one question
ask question backend="loop":
    uv run --project clients/cli voltrieve ask "{{question}}" --backend {{backend}} --az-token

# Run the web client locally against the deployed API, on http://localhost:5173
web:
    cd clients/web && npm ci --silent && npm run dev -- --mode production

# Run the operations dashboard locally against the deployed API, on http://localhost:5174
dashboard:
    cd clients/dashboard && npm ci --silent && npm run dev -- --mode production

# Run the car locally against the deployed API, on http://localhost:5175
car:
    cd clients/car && npm ci --silent && npm run dev -- --mode production

# Run the dealer page locally against the deployed dealer service, on http://localhost:5176
dealer:
    cd services/dealer/site && npm ci --silent && DEALER_URL="$(azd env get-value DEALER_URL)" npm run dev

# Open the deployed operations dashboard in your browser
open-dashboard:
    open "$(azd env get-value DASHBOARD_URL)"

# Open the deployed web client in your browser
open:
    open "$(azd env get-value WEB_URL)"

# Open the deployed car in your browser
open-car:
    open "$(azd env get-value CAR_URL)"

# Open the deployed dealer page in your browser: orders and inventory
open-dealer:
    open "$(azd env get-value DEALER_URL)"

# Wake every service before a demo. They sleep again after 5 idle minutes
warm-up:
    uv run scripts/warm_up.py

# Wake every service and keep them awake until Ctrl-C
keep-warm:
    uv run scripts/warm_up.py --keep-warm
