# Zoom Link Extractor Bot

A Telegram bot that converts Zoom recording share links into actual playable links.

## Features

- Convert Zoom share links to playable links
- Batch processing for multiple links
- Two processing modes: Sample (10 links) and Full (all links)
- Handles large files with 200+ links
- Deployed on Render

## Setup

1. Create a Telegram bot using [@BotFather](https://t.me/BotFather)
2. Get your bot token
3. Set `BOT_TOKEN` environment variable in Render
4. Deploy to Render

## Usage

1. Send `/start` to the bot
2. Upload a text file with Zoom links in this format:
