from django.db import models

# Create your models here.
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

class Campaign(models.Model):
    CATEGORY_CHOICES = [
        ('personal', 'Personal'),
        ('business', 'Business'),
        ('charity', 'Charity'),
        ('community', 'Community'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    title = models.CharField(max_length=200)
    description = models.TextField()
    target_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(1)])
    raised_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_campaigns')
    image = models.ImageField(upload_to='campaigns/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Escrow fields
    escrow_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_withdrawn = models.BooleanField(default=False)
    
    def __str__(self):
        return self.title
    
    class Meta:
        ordering = ['-created_at']
    
    @property
    def progress_percentage(self):
        if self.target_amount > 0:
            return (self.raised_amount / self.target_amount) * 100
        return 0
    
    @property
    def days_remaining(self):
        from django.utils import timezone
        if self.end_date > timezone.now():
            return (self.end_date - timezone.now()).days
        return 0
    
    def release_funds_to_creator(self, bank_details=None):
        """
        Release escrow funds to creator's bank account or Raenest
        """
        if self.status != 'completed':
            return False, "Campaign must be completed to release funds"
        
        if self.is_withdrawn:
            return False, "Funds already withdrawn from this campaign"
        
        if self.escrow_balance <= 0:
            return False, "No funds in escrow to release"
        
        # Here you would integrate with Paystack/Raenest to send money to bank
        # For now, we'll just mark as withdrawn
        
        self.is_withdrawn = True
        self.save()
        
        return True, f"Successfully released {self.escrow_balance} from escrow"

class Contribution(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]
    
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='contributions')
    contributor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    message = models.TextField(blank=True, null=True)
    is_anonymous = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_reference = models.CharField(max_length=100, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.contributor.username} - {self.campaign.title} - ${self.amount}"
    
    class Meta:
        ordering = ['-created_at']