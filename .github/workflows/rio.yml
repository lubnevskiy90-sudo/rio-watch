name: rio-watch

on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: rio-watch
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install
        run: pip install requests

      - name: Run
        env:
          TG_TOKEN: ${{ secrets.TG_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
        run: python rio_watch.py --debug

      - name: Save seen.json
        run: |
          if [ -n "$(git status --porcelain seen.json)" ]; then
            git config user.name "rio-bot"
            git config user.email "rio-bot@users.noreply.github.com"
            git add seen.json
            git commit -m "update seen"
            git push
          fi
