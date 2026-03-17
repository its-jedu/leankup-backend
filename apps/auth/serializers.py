from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core import exceptions
from django.contrib.auth import authenticate
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from phonenumber_field.serializerfields import PhoneNumberField

class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)
    phone_number = PhoneNumberField(required=False)
    
    class Meta:
        model = User
        fields = ('username', 'password', 'password2', 'email', 'first_name', 'last_name', 'phone_number')
        extra_kwargs = {
            'first_name': {'required': True},
            'last_name': {'required': True},
            'email': {'required': True}
        }
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        
        # Check if email already exists
        if User.objects.filter(email=attrs['email']).exists():
            raise serializers.ValidationError({"email": "A user with this email already exists."})
        
        return attrs
    
    def create(self, validated_data):
        validated_data.pop('password2')
        phone_number = validated_data.pop('phone_number', None)
        
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name']
        )
        
        # The signal in apps.users.models already creates the profile
        # We just need to update the phone number if provided
        if phone_number:
            from apps.users.models import Profile
            Profile.objects.filter(user=user).update(phone_number=phone_number)
        
        return user

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        credentials = {
            'username': attrs.get('username'),
            'password': attrs.get('password')
        }
        
        # Allow login with email
        if '@' in credentials['username']:
            try:
                # Use filter() instead of get() to handle multiple users
                users = User.objects.filter(email=credentials['username'])
                if users.exists():
                    if users.count() > 1:
                        # If multiple users with same email, require username
                        raise serializers.ValidationError({
                            "username": "Multiple users found with this email. Please login with username instead."
                        })
                    credentials['username'] = users.first().username
                else:
                    raise serializers.ValidationError({
                        "email": "No user found with this email."
                    })
            except User.DoesNotExist:
                raise serializers.ValidationError({
                    "email": "No user found with this email."
                })
        
        return super().validate(credentials)

class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    
    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email does not exist.")
        return value