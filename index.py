import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from web3 import Web3
from eth_account import Account

app = Flask(__name__)
CORS(app)

# --- Configuration (Polygon Amoy Testnet) ---
# Ensure these environment variables are set in Vercel
PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY") 
PLATFORM_FEE_ADDRESS = "0x2b01E2C0024aaF8362a11f91A545F24cB5e5261f"
USDC_CONTRACT_ADDRESS = "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582" # Amoy USDC

# Amoy RPC Providers for Fallback logic
AMOY_PROVIDERS = [
    "https://rpc-amoy.polygon.technology",
    "https://polygon-amoy-bor-rpc.publicnode.com",
    "https://rpc.ankr.com/polygon_amoy"
]

# Minimal ERC20 ABI for Transfer and Decimals
ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
]

def get_web3_connection():
    """Attempts to connect to Amoy using the provider list."""
    for url in AMOY_PROVIDERS:
        w3 = Web3(Web3.HTTPProvider(url))
        if w3.is_connected():
            return w3
    return None

@app.route('/api/send', methods=['POST'])
def handle_transfer():
    try:
        # 1. Parse Input from LynkPay Frontend
        data = request.json
        recipient = data.get("recipient_address")
        amount_raw = float(data.get("amount")) # The total amount user entered
        
        w3 = get_web3_connection()
        if not w3:
            return jsonify({"error": "Unable to connect to Polygon Amoy nodes"}), 503

        # 2. Setup Account and Contract
        account = Account.from_key(PRIVATE_KEY)
        usdc_contract = w3.eth.contract(address=w3.to_checksum_address(USDC_CONTRACT_ADDRESS), abi=ERC20_ABI)
        
        # 3. Calculate Amounts (USDC = 6 decimals)
        decimals = usdc_contract.functions.decimals().call()
        total_units = int(amount_raw * (10**decimals))
        
        # 2% Fee Logic
        fee_units = int(total_units * 0.02)
        user_transfer_units = total_units - fee_units

        # 4. Transaction Preparation
        # We fetch the current nonce for the sender wallet
        nonce = w3.eth.get_transaction_count(account.address)
        base_fee = w3.eth.gas_price
        
        # Transaction A: Send 98% to Recipient
        tx_recipient = usdc_contract.functions.transfer(
            w3.to_checksum_address(recipient), 
            user_transfer_units
        ).build_transaction({
            'chainId': 80002, # Amoy Chain ID
            'gas': 100000,
            'gasPrice': int(base_fee * 1.2), # 20% buffer for faster inclusion
            'nonce': nonce,
        })

        # Transaction B: Send 2% to LynkPay Fee Address
        tx_fee = usdc_contract.functions.transfer(
            w3.to_checksum_address(PLATFORM_FEE_ADDRESS), 
            fee_units
        ).build_transaction({
            'chainId': 80002,
            'gas': 100000,
            'gasPrice': int(base_fee * 1.2),
            'nonce': nonce + 1,
        })

        # 5. Cryptographic Signing (Python-side)
        signed_tx_recipient = w3.eth.account.sign_transaction(tx_recipient, PRIVATE_KEY)
        signed_tx_fee = w3.eth.account.sign_transaction(tx_fee, PRIVATE_KEY)

        # 6. Broadcast to Amoy
        tx_hash_main = w3.eth.send_raw_transaction(signed_tx_recipient.rawTransaction)
        tx_hash_fee = w3.eth.send_raw_transaction(signed_tx_fee.rawTransaction)

        return jsonify({
            "status": "success",
            "network": "Polygon Amoy",
            "recipient_tx": w3.to_hex(tx_hash_main),
            "fee_tx": w3.to_hex(tx_hash_fee),
            "breakdown": {
                "sent_to_user": user_transfer_units / (10**decimals),
                "lynkpay_fee": fee_units / (10**decimals)
            }
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

# Vercel requires the app instance
app = app
