from rest_framework import serializers
from django.core.validators import MinValueValidator
from .models import Wallet, Transaction

class WalletSerializer(serializers.ModelSerializer):
    username = serializers.ReadOnlyField(source='user.username')
    
    class Meta:
        model = Wallet
        fields = ['id', 'username', 'balance', 'created_at', 'updated_at']
        read_only_fields = ['balance']

class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = '__all__'
        read_only_fields = ['wallet', 'reference', 'created_at', 'updated_at']

class WithdrawalSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    bank_name = serializers.CharField(max_length=100)
    bank_account_number = serializers.CharField(max_length=20)
    bank_account_name = serializers.CharField(max_length=200)
    bank_code = serializers.CharField(max_length=10, required=False)
    
    def validate_amount(self, value):
        user = self.context['request'].user
        wallet = Wallet.objects.get(user=user)
        
        if wallet.balance < value:
            raise serializers.ValidationError("Insufficient balance")
        
        return value