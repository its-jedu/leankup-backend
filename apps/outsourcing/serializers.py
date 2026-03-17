from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Task, Application

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
    
    class Meta:
        model = Task
        fields = '__all__'
    
    def get_creator(self, obj):
        return {
            'id': obj.creator.id,
            'username': obj.creator.username,
            'first_name': obj.creator.first_name,
            'last_name': obj.creator.last_name,
        }
    
    def get_applications(self, obj):
        # Only show applications to creator or admin
        request = self.context.get('request')
        if request and (request.user == obj.creator or request.user.is_staff):
            applications = obj.applications.all()
            return ApplicationSerializer(applications, many=True, context=self.context).data
        # For non-creators, return empty list instead of null
        return []
    
    def get_applications_count(self, obj):
        return obj.applications.count()

class ApplicationSerializer(serializers.ModelSerializer):
    applicant_username = serializers.ReadOnlyField(source='applicant.username')
    applicant_name = serializers.SerializerMethodField()
    task_title = serializers.ReadOnlyField(source='task.title')
    
    class Meta:
        model = Application
        fields = '__all__'
        read_only_fields = ['applicant', 'created_at', 'updated_at']
    
    def get_applicant_name(self, obj):
        return f"{obj.applicant.first_name} {obj.applicant.last_name}"
    
    def validate(self, data):
        # Check if user already applied
        request = self.context.get('request')
        task = data.get('task') or self.instance.task if self.instance else None
        
        if request and request.method == 'POST':
            if Application.objects.filter(task=task, applicant=request.user).exists():
                raise serializers.ValidationError("You have already applied to this task")
        
        return data