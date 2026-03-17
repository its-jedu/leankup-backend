import requests
import hmac
import hashlib
from django.conf import settings
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class PaystackService:
    def __init__(self):
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self.base_url = "https://api.paystack.co"
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json"
        }
    
    def initialize_payment(self, email, amount, reference, metadata=None):
        """
        Initialize a payment transaction
        """
        url = f"{self.base_url}/transaction/initialize"
        
        # Convert amount to kobo (Paystack uses smallest currency unit)
        amount_in_kobo = int(amount * 100)
        
        data = {
            "email": email,
            "amount": amount_in_kobo,
            "reference": reference,
            "metadata": metadata or {}
        }
        
        try:
            response = requests.post(url, json=data, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Paystack initialization error: {str(e)}")
            return {"status": False, "message": str(e)}
    
    def verify_payment(self, reference):
        """
        Verify a payment transaction
        """
        url = f"{self.base_url}/transaction/verify/{reference}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Paystack verification error: {str(e)}")
            return {"status": False, "message": str(e)}
    
    def initiate_transfer(self, amount, recipient_code, reference, reason=None):
        """
        Initiate a transfer to a bank account
        """
        url = f"{self.base_url}/transfer"
        
        amount_in_kobo = int(amount * 100)
        
        data = {
            "source": "balance",
            "amount": amount_in_kobo,
            "recipient": recipient_code,
            "reference": reference,
            "reason": reason or "Withdrawal from LeankUp"
        }
        
        try:
            response = requests.post(url, json=data, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Paystack transfer error: {str(e)}")
            return {"status": False, "message": str(e)}
    
    def create_transfer_recipient(self, name, account_number, bank_code, currency="NGN"):
        """
        Create a transfer recipient (bank account)
        """
        url = f"{self.base_url}/transferrecipient"
        
        data = {
            "type": "nuban",
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": currency
        }
        
        try:
            response = requests.post(url, json=data, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Paystack create recipient error: {str(e)}")
            return {"status": False, "message": str(e)}
    
    def verify_webhook_signature(self, request):
        """
        Verify Paystack webhook signature
        """
        signature = request.headers.get('x-paystack-signature')
        if not signature:
            return False
        
        # Compute hash
        hash = hmac.new(
            self.secret_key.encode('utf-8'),
            request.body,
            hashlib.sha512
        ).hexdigest()
        
        return hmac.compare_digest(hash, signature)