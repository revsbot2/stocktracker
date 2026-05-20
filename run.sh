#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

echo "Installing / verifying dependencies..."
pip install -q -r requirements.txt

echo ""
echo "  Stock Tracker running at http://localhost:3001"
echo "  Press Ctrl+C to stop."
echo ""

python app.py
