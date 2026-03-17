from django.shortcuts import render

# Create your views here.
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.db import transaction, models
from decimal import Decimal
from .models import Campaign, Contribution
from .serializers import CampaignSerializer, CampaignDetailSerializer, ContributionSerializer
from apps.core.permissions import IsCreatorOrReadOnly
from apps.payments.models import Payment
from apps.wallet.models import Wallet, Transaction
import uuid


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
        
        # Auto-update campaign status based on end date
        now = timezone.now()
        for campaign in queryset.filter(status='active', end_date__lt=now):
            campaign.status = 'completed'
            campaign.save()
        
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
            # Auto-update campaign status
            campaign.status = 'completed'
            campaign.save()
            return Response(
                {'error': 'This campaign has ended'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate amount
        amount = Decimal(str(request.data.get('amount', 0)))
        if amount <= 0:
            return Response(
                {'error': 'Amount must be greater than 0'},
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
            
            # Set callback URL
            callback_url = f"{request.build_absolute_uri('/').rstrip('/')}/api/payments/verify/"
            
            payment_data = payment_service.initialize_payment(
                email=request.user.email,
                amount=contribution.amount,
                reference=contribution_ref,
                metadata={
                    'campaign_id': campaign.id,
                    'contribution_id': contribution.id,
                    'user_id': request.user.id,
                    'message': request.data.get('message', ''),
                    'is_anonymous': request.data.get('is_anonymous', False),
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
                    },
                    'message': 'Payment initialized. Money will be held in escrow until campaign ends.'
                }, status=status.HTTP_201_CREATED)
            
            # If payment initialization failed, delete the contribution
            contribution.delete()
            return Response(
                {'error': 'Failed to initialize payment. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'])
    def contributions(self, request, pk=None):
        campaign = self.get_object()
        contributions = campaign.contributions.filter(status='paid').order_by('-created_at')
        serializer = ContributionSerializer(contributions, many=True)
        
        # Add summary statistics
        total_contributors = contributions.values('contributor').distinct().count()
        average_contribution = contributions.aggregate(avg=models.Avg('amount'))['avg'] or 0
        
        return Response({
            'contributions': serializer.data,
            'summary': {
                'total_count': contributions.count(),
                'total_amount': str(campaign.raised_amount),
                'unique_contributors': total_contributors,
                'average_contribution': str(round(average_contribution, 2))
            }
        })
    
    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_escrow(self, request):
        """
        Get all escrow balances for user's campaigns
        """
        campaigns = Campaign.objects.filter(creator=request.user).order_by('-created_at')
        
        total_escrow = 0
        active_campaigns = []
        completed_campaigns_awaiting = []
        withdrawn_campaigns = []
        
        for campaign in campaigns:
            progress = 0
            if campaign.target_amount > 0:
                progress = (campaign.raised_amount / campaign.target_amount) * 100
            
            campaign_data = {
                'id': campaign.id,
                'title': campaign.title,
                'escrow_balance': str(campaign.escrow_balance),
                'raised_amount': str(campaign.raised_amount),
                'target_amount': str(campaign.target_amount),
                'progress': round(progress, 2),
                'status': campaign.status,
                'is_withdrawn': campaign.is_withdrawn,
                'end_date': campaign.end_date,
                'days_remaining': campaign.days_remaining,
                'created_at': campaign.created_at
            }
            
            if campaign.status == 'active':
                total_escrow += campaign.escrow_balance
                active_campaigns.append(campaign_data)
            elif campaign.status == 'completed' and not campaign.is_withdrawn:
                total_escrow += campaign.escrow_balance
                completed_campaigns_awaiting.append(campaign_data)
            else:
                withdrawn_campaigns.append(campaign_data)
        
        return Response({
            'total_in_escrow': str(total_escrow),
            'summary': {
                'total_campaigns': campaigns.count(),
                'active_campaigns': len(active_campaigns),
                'awaiting_withdrawal': len(completed_campaigns_awaiting),
                'withdrawn': len(withdrawn_campaigns)
            },
            'active_campaigns': active_campaigns,
            'completed_campaigns_awaiting_withdrawal': completed_campaigns_awaiting,
            'withdrawn_campaigns': withdrawn_campaigns
        })
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    @transaction.atomic
    def release_funds(self, request, pk=None):
        """
        Creator releases escrow funds to their bank account after campaign ends
        """
        campaign = self.get_object()
        
        # Only creator can release funds
        if request.user != campaign.creator and not request.user.is_staff:
            return Response(
                {'error': 'Only the campaign creator can release funds'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Auto-update campaign status if ended
        if campaign.status == 'active' and campaign.end_date < timezone.now():
            campaign.status = 'completed'
            campaign.save()
        
        # Check if campaign is completed
        if campaign.status != 'completed':
            return Response(
                {'error': 'Campaign must be completed to release funds. Current status: ' + campaign.status},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if funds already released
        if campaign.is_withdrawn:
            return Response(
                {'error': 'Funds have already been withdrawn from this campaign'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if there are funds to release
        if campaign.escrow_balance <= 0:
            return Response(
                {'error': 'No funds in escrow to release'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get bank details from request or user profile
        bank_details = {
            'bank_name': request.data.get('bank_name') or request.user.profile.bank_name,
            'bank_account_number': request.data.get('bank_account_number') or request.user.profile.bank_account_number,
            'bank_account_name': request.data.get('bank_account_name') or request.user.profile.bank_account_name,
            'bank_code': request.data.get('bank_code') or request.user.profile.bank_code,
        }
        
        # Validate bank details
        missing_fields = []
        for key, value in bank_details.items():
            if key != 'bank_code' and not value:  # bank_code is optional
                missing_fields.append(key.replace('_', ' ').title())
        
        if missing_fields:
            return Response(
                {
                    'error': 'Bank details are required',
                    'missing_fields': missing_fields,
                    'message': 'Please update your profile with bank details or provide them in the request.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create a transaction record for the creator's wallet (for record keeping)
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        
        transaction_ref = f"ESCROW_RELEASE_{campaign.id}_{uuid.uuid4().hex[:10].upper()}"
        
        transaction_record = Transaction.objects.create(
            wallet=wallet,
            amount=campaign.escrow_balance,
            transaction_type='credit',
            status='completed',
            reference=transaction_ref,
            description=f"Funds released from campaign: {campaign.title}",
            metadata={
                'campaign_id': campaign.id,
                'campaign_title': campaign.title,
                'bank_details': {
                    'bank_name': bank_details['bank_name'],
                    'bank_account_number': bank_details['bank_account_number'][-4:],  # Only last 4 digits for security
                    'bank_account_name': bank_details['bank_account_name']
                },
                'escrow_amount': str(campaign.escrow_balance),
                'release_date': timezone.now().isoformat()
            }
        )
        
        # Mark campaign as withdrawn
        campaign.is_withdrawn = True
        campaign.save()
        
        # Here you would call the payment service to actually transfer money
        # from_paystack_service = PaystackService()
        # transfer_result = from_paystack_service.initiate_transfer(
        #     amount=campaign.escrow_balance,
        #     recipient_code=bank_details.get('recipient_code'),
        #     reference=transaction_record.reference
        # )
        
        return Response({
            'success': True,
            'message': f'Successfully released ₦{campaign.escrow_balance:,.2f} from escrow',
            'amount': str(campaign.escrow_balance),
            'campaign': {
                'id': campaign.id,
                'title': campaign.title,
                'total_raised': str(campaign.raised_amount)
            },
            'transaction': {
                'reference': transaction_record.reference,
                'amount': str(transaction_record.amount),
                'date': transaction_record.created_at
            },
            'bank_details': {
                'bank_name': bank_details['bank_name'],
                'bank_account_number': '****' + bank_details['bank_account_number'][-4:],
                'bank_account_name': bank_details['bank_account_name']
            }
        })
    
    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def escrow_status(self, request, pk=None):
        """
        Check escrow status for a specific campaign
        """
        campaign = self.get_object()
        
        # Auto-update campaign status if ended
        if campaign.status == 'active' and campaign.end_date < timezone.now():
            campaign.status = 'completed'
            campaign.save()
        
        # Only creator can view escrow status
        if request.user != campaign.creator and not request.user.is_staff:
            return Response(
                {'error': 'Only the campaign creator can view escrow status'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get recent contributions
        recent_contributions = campaign.contributions.filter(
            status='paid'
        ).order_by('-created_at')[:5]
        
        contribution_summary = campaign.contributions.filter(
            status='paid'
        ).aggregate(
            total=models.Count('id'),
            unique_contributors=models.Count('contributor', distinct=True),
            average=models.Avg('amount')
        )
        
        return Response({
            'campaign_id': campaign.id,
            'campaign_title': campaign.title,
            'campaign_status': campaign.status,
            'escrow_balance': str(campaign.escrow_balance),
            'raised_amount': str(campaign.raised_amount),
            'target_amount': str(campaign.target_amount),
            'progress_percentage': round(campaign.progress_percentage, 2),
            'is_withdrawn': campaign.is_withdrawn,
            'can_withdraw': campaign.status == 'completed' and not campaign.is_withdrawn and campaign.escrow_balance > 0,
            'days_remaining': campaign.days_remaining,
            'end_date': campaign.end_date,
            'start_date': campaign.start_date,
            'contribution_stats': {
                'total_contributions': contribution_summary['total'] or 0,
                'unique_contributors': contribution_summary['unique_contributors'] or 0,
                'average_contribution': str(round(contribution_summary['average'] or 0, 2))
            },
            'recent_contributions': ContributionSerializer(recent_contributions, many=True).data
        })

