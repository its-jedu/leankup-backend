from django.shortcuts import render

# Create your views here.
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.db import transaction
from .models import Campaign, Contribution
from .serializers import CampaignSerializer, CampaignDetailSerializer, ContributionSerializer
from apps.core.permissions import IsCreatorOrReadOnly
from apps.payments.models import Payment

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
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    @transaction.atomic
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
            
            # Generate unique references
            contribution_ref = f"CONTRIB_{campaign.id}_{contribution.id}"
            payment_ref = f"PAY_{contribution.id}_{campaign.id}"
            
            # Initialize payment with Paystack
            from apps.payments.services import PaystackService
            payment_service = PaystackService()
            
            # Set callback URL to your ngrok URL
            callback_url = f"{request.build_absolute_uri('/').rstrip('/')}/api/payments/verify/"
            
            payment_data = payment_service.initialize_payment(
                email=request.user.email,
                amount=contribution.amount,
                reference=contribution_ref,
                metadata={
                    'campaign_id': campaign.id,
                    'contribution_id': contribution.id,
                    'user_id': request.user.id,
                    'callback_url': callback_url
                }
            )
            
            if payment_data['status']:
                # Update contribution with payment reference
                contribution.payment_reference = payment_data['data']['reference']
                contribution.save()
                
                # Create payment record in database
                payment = Payment.objects.create(
                    user=request.user,
                    amount=contribution.amount,
                    payment_type='contribution',
                    status='pending',
                    reference=payment_ref,
                    paystack_reference=payment_data['data']['reference'],
                    metadata={
                        'campaign_id': campaign.id,
                        'contribution_id': contribution.id,
                        'authorization_url': payment_data['data']['authorization_url']
                    }
                )
                
                return Response({
                    'contribution': serializer.data,
                    'payment': {
                        'authorization_url': payment_data['data']['authorization_url'],
                        'access_code': payment_data['data']['access_code'],
                        'reference': payment_data['data']['reference'],
                        'payment_id': payment.id
                    }
                }, status=status.HTTP_201_CREATED)
            
            # If payment initialization failed, delete the contribution
            contribution.delete()
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