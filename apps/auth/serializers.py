from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
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
        
        # Check if username already exists
        if User.objects.filter(username=attrs['username']).exists():
            raise serializers.ValidationError({"username": "A user with this username already exists."})
        
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
        # Get the request from the context
        request = self.context.get('request')
        username_or_email = attrs.get('username')
        password = attrs.get('password')
        
        # Determine if input is email or username
        if '@' in username_or_email:
            try:
                # Try to find user by email
                user = User.objects.get(email=username_or_email)
                username = user.username
            except User.DoesNotExist:
                raise serializers.ValidationError({
                    "detail": "No active account found with the given credentials"
                })
        else:
            username = username_or_email
        
        # Authenticate with username and pass the request for axes
        user = authenticate(request=request, username=username, password=password)
        
        if not user:
            raise serializers.ValidationError({
                "detail": "No active account found with the given credentials"
            })
        
        if not user.is_active:
            raise serializers.ValidationError({
                "detail": "This account is inactive"
            })
        
        # Generate tokens
        refresh = self.get_token(user)
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
            }
        }
        
        return data

class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    
    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email does not exist.")
        return value