from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Profile

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'date_joined')

class ProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    wallet_balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    class Meta:
        model = Profile
        fields = (
            'id', 'user', 'phone_number', 'raenest_account_id', 
            'bank_account_name', 'bank_account_number', 'bank_name',
            'bank_code', 'bio', 'location', 'avatar',
            'total_tasks_posted', 'total_tasks_completed', 
            'total_campaigns_created', 'total_earned', 'response_rate',
            'wallet_balance', 'created_at', 'updated_at'
        )
        read_only_fields = [
            'total_tasks_posted', 'total_tasks_completed', 
            'total_campaigns_created', 'total_earned', 'response_rate',
            'created_at', 'updated_at'
        ]
    
    def update(self, instance, validated_data):
        # Only allow updating certain fields
        allowed_fields = [
            'phone_number', 'raenest_account_id', 
            'bank_account_name', 'bank_account_number', 
            'bank_name', 'bank_code', 'bio', 'location', 'avatar'
        ]
        
        for field in allowed_fields:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        
        instance.save()
        return instance