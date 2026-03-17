from django.shortcuts import render

# Create your views here.
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from .models import Campaign, Contribution
from .serializers import CampaignSerializer, CampaignDetailSerializer, ContributionSerializer
from apps.core.permissions import IsCreatorOrReadOnly

class CampaignViewSet(viewsets.ModelViewSet):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsCreatorOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'status', 'creator']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'target_amount', 'end_date']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return CampaignDetailSerializer
        return CampaignSerializer
    
    def get_queryset(self):
        queryset = Campaign.objects.all()
        
        # Filter active campaigns
        active_only = self.request.query_params.get('active_only', None)
        if active_only:
            queryset = queryset.filter(
                status='active',
                start_date__lte=timezone.now(),
                end_date__gte=timezone.now()
            )
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)
    
    @action(detail=True, methods=['post'])
    def contribute(self, request, pk=None):
        campaign = self.get_object()
        
        # Check if campaign is active
        if campaign.status != 'active':
            return Response(
                {'error': 'This campaign is not accepting contributions'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if campaign.end_date < timezone.now():
            return Response(
                {'error': 'This campaign has ended'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create contribution
        serializer = ContributionSerializer(
            data={'campaign': campaign.id, **request.data},
            context={'request': request}
        )
        
        if serializer.is_valid():
            contribution = serializer.save(contributor=request.user, campaign=campaign)
            
            # Initialize payment
            from apps.payments.services import PaystackService
            payment_service = PaystackService()
            payment_data = payment_service.initialize_payment(
                email=request.user.email,
                amount=contribution.amount,
                reference=f"CONTRIB_{campaign.id}_{contribution.id}"
            )
            
            if payment_data['status']:
                contribution.payment_reference = payment_data['data']['reference']
                contribution.save()
                
                return Response({
                    'contribution': serializer.data,
                    'payment': payment_data['data']
                }, status=status.HTTP_201_CREATED)
            
            return Response(
                {'error': 'Failed to initialize payment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'])
    def contributions(self, request, pk=None):
        campaign = self.get_object()
        contributions = campaign.contributions.filter(status='paid')
        serializer = ContributionSerializer(contributions, many=True)
        return Response(serializer.data)