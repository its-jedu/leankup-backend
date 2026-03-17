from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Campaign, Contribution

class CampaignSerializer(serializers.ModelSerializer):
    creator_username = serializers.ReadOnlyField(source='creator.username')
    progress_percentage = serializers.ReadOnlyField()
    days_remaining = serializers.ReadOnlyField()
    contributors_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Campaign
        fields = '__all__'
        read_only_fields = ['creator', 'raised_amount', 'created_at', 'updated_at']
    
    def get_contributors_count(self, obj):
        return obj.contributions.filter(status='paid').values('contributor').distinct().count()
    
    def validate(self, data):
        if data.get('start_date') and data.get('end_date'):
            if data['start_date'] >= data['end_date']:
                raise serializers.ValidationError("End date must be after start date")
        return data

class CampaignDetailSerializer(serializers.ModelSerializer):
    creator = serializers.SerializerMethodField()
    recent_contributions = serializers.SerializerMethodField()
    
    class Meta:
        model = Campaign
        fields = '__all__'
    
    def get_creator(self, obj):
        return {
            'id': obj.creator.id,
            'username': obj.creator.username,
            'first_name': obj.creator.first_name,
            'last_name': obj.creator.last_name,
        }
    
    def get_recent_contributions(self, obj):
        contributions = obj.contributions.filter(status='paid')[:10]
        return ContributionSerializer(contributions, many=True).data

class ContributionSerializer(serializers.ModelSerializer):
    contributor_name = serializers.SerializerMethodField()
    campaign_title = serializers.ReadOnlyField(source='campaign.title')
    
    class Meta:
        model = Contribution
        fields = '__all__'
        read_only_fields = ['contributor', 'status', 'created_at', 'updated_at']
    
    def get_contributor_name(self, obj):
        if obj.is_anonymous:
            return 'Anonymous'
        return f"{obj.contributor.first_name} {obj.contributor.last_name}"