"""
ai_engine/orchestrator/tools/crypto_tools.py
─────────────────────────────────────────────────────────────────────────────
Blockchain / Crypto Agent Tools.
Handles WALLET leads using Etherscan and Blockchain.com.
"""

import os
import requests

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")

def get_eth_balance(wallet: str) -> str:
    """Gets balance and transaction count for an Ethereum wallet."""
    if not ETHERSCAN_API_KEY:
        return f"[Etherscan Info] ETHERSCAN_API_KEY not configured. Skipping ETH check for {wallet}."
        
    # Only check if it looks like an ETH wallet
    if not wallet.startswith("0x"):
        return ""
        
    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "balance",
        "address": wallet,
        "tag": "latest",
        "apikey": ETHERSCAN_API_KEY
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if data.get("status") == "1":
            balance_wei = int(data.get("result", 0))
            balance_eth = balance_wei / 1e18
            return f"=== Ethereum Wallet Data for {wallet} ===\nCurrent Balance: {balance_eth:.4f} ETH\n"
        else:
            return f"[Etherscan Error] {data.get('message')}: {data.get('result')}"
    except Exception as e:
        return f"[Etherscan Error for {wallet}]: {e}"

def get_btc_balance(wallet: str) -> str:
    """Gets balance for a Bitcoin wallet using Blockchain.info (Free)."""
    # Only check if it looks like a BTC wallet
    if wallet.startswith("0x"):
        return ""
        
    url = f"https://blockchain.info/q/addressbalance/{wallet}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            satoshi = int(response.text)
            btc = satoshi / 1e8
            return f"=== Bitcoin Wallet Data for {wallet} ===\nCurrent Balance: {btc:.6f} BTC\n"
        else:
            return f"[Blockchain.com Error] API returned status {response.status_code}."
    except Exception as e:
        return f"[Blockchain.com Error for {wallet}]: {e}"
