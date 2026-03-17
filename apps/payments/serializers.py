from rest_framework import serializers
from django.core.validators import MinValueValidator
from .models import Payment
from apps.fundraising.models import Contribution

class PaymentInitializeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    payment_type = serializers.ChoiceField(choices=['contribution', 'task_payment'])
    campaign_id = serializers.IntegerField(required=False)
    task_id = serializers.IntegerField(required=False)
    
    def validate(self, data):
        if data['payment_type'] == 'contribution' and not data.get('campaign_id'):
            raise serializers.ValidationError("campaign_id is required for contributions")
        
        if data['payment_type'] == 'task_payment' and not data.get('task_id'):
            raise serializers.ValidationError("task_id is required for task payments")
        
        return data

class PaymentVerifySerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100)

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = '__all__'
        read_only_fields = ['user', 'created_at', 'updated_at']