from rest_framework import serializers
from django.contrib.auth.models import User
from django.db import models
from .models import Task, Application, ChatMessage, Notification
from apps.users.models import Profile
from apps.users.serializers import ProfileSerializer

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['bio', 'location', 'avatar', 'total_tasks_posted', 
                  'total_tasks_completed', 'total_campaigns_created', 
                  'total_earned', 'response_rate']

class TaskSerializer(serializers.ModelSerializer):
    creator_username = serializers.ReadOnlyField(source='creator.username')
    applications_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = '__all__'
        read_only_fields = ['creator', 'created_at', 'updated_at']
    
    def get_applications_count(self, obj):
        return obj.applications.count()

class TaskDetailSerializer(serializers.ModelSerializer):
    creator = serializers.SerializerMethodField()
    applications = serializers.SerializerMethodField()
    applications_count = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = '__all__'
    
    def get_creator(self, obj):
        profile = Profile.objects.get(user=obj.creator) if hasattr(obj.creator, 'profile') else None
        return {
            'id': obj.creator.id,
            'username': obj.creator.username,
            'first_name': obj.creator.first_name,
            'last_name': obj.creator.last_name,
            'email': obj.creator.email,
            'date_joined': obj.creator.date_joined,
            'profile': ProfileSerializer(profile).data if profile else None
        }
    
    def get_applications(self, obj):
        request = self.context.get('request')
        if request and (request.user == obj.creator or request.user.is_staff):
            applications = obj.applications.all()
            return ApplicationSerializer(applications, many=True, context=self.context).data
        return []
    
    def get_applications_count(self, obj):
        return obj.applications.count()
    
    def get_messages(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            messages = obj.messages.filter(
                models.Q(sender=request.user) | models.Q(receiver=request.user)
            )
            return ChatMessageSerializer(messages, many=True).data
        return []

class ApplicationSerializer(serializers.ModelSerializer):
    applicant_username = serializers.ReadOnlyField(source='applicant.username')
    applicant_name = serializers.SerializerMethodField()
    task_title = serializers.ReadOnlyField(source='task.title')
    task_id = serializers.ReadOnlyField(source='task.id')
    
    class Meta:
        model = Application
        fields = '__all__'
        read_only_fields = ['applicant', 'created_at', 'updated_at']
    
    def get_applicant_name(self, obj):
        return f"{obj.applicant.first_name} {obj.applicant.last_name}"
    
    def validate(self, data):
        request = self.context.get('request')
        task = data.get('task') or self.instance.task if self.instance else None
        
        if request and request.method == 'POST':
            if Application.objects.filter(task=task, applicant=request.user).exists():
                raise serializers.ValidationError("You have already applied to this task")
        
        return data

class ChatMessageSerializer(serializers.ModelSerializer):
    sender_username = serializers.ReadOnlyField(source='sender.username')
    receiver_username = serializers.ReadOnlyField(source='receiver.username')
    
    class Meta:
        model = ChatMessage
        fields = '__all__'
        read_only_fields = ['sender', 'created_at', 'is_read']
    
    def validate(self, data):
        task = data.get('task')
        sender = self.context.get('request').user
        
        # Check if user is authorized to send messages for this task
        is_creator = task.creator == sender
        has_accepted_application = task.applications.filter(
            applicant=sender, status='accepted'
        ).exists()
        
        if not is_creator and not has_accepted_application:
            raise serializers.ValidationError(
                "You are not authorized to send messages for this task"
            )
        
        # Set receiver automatically
        if is_creator:
            # Creator sending to the accepted applicant
            accepted_app = task.applications.filter(status='accepted').first()
            if accepted_app:
                data['receiver'] = accepted_app.applicant
        else:
            # Applicant sending to creator
            data['receiver'] = task.creator
        
        return data

class NotificationSerializer(serializers.ModelSerializer):
    sender_username = serializers.ReadOnlyField(source='sender.username')
    task_title = serializers.ReadOnlyField(source='task.title')
    
    class Meta:
        model = Notification
        fields = '__all__'
        read_only_fields = ['recipient', 'created_at', 'is_read']