from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator, MinValueValidator
from django.db.models.signals import post_save
from django.dispatch import receiver
import secrets

class Task(models.Model):
    CATEGORY_CHOICES = [
        ('delivery', 'Delivery'),
        ('cleaning', 'Cleaning'),
        ('moving', 'Moving'),
        ('repair', 'Repair'),
        ('tutoring', 'Tutoring'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    title = models.CharField(max_length=200)
    description = models.TextField(validators=[MinLengthValidator(10)])
    location = models.CharField(max_length=255)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_tasks')
    budget = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    completion_key = models.CharField(max_length=100, blank=True, null=True, unique=True)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_tasks')
    completed_at = models.DateTimeField(blank=True, null=True)
    
    def __str__(self):
        return self.title
    
    def generate_completion_key(self):
        """Generate a unique completion key for the task"""
        return secrets.token_urlsafe(32)
    
    class Meta:
        ordering = ['-created_at']

class Application(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ]
    
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='applications')
    applicant = models.ForeignKey(User, on_delete=models.CASCADE, related_name='task_applications')
    message = models.TextField()
    portfolio_link = models.URLField(blank=True, null=True)
    proposed_budget = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(blank=True, null=True)
    completion_key_used = models.CharField(max_length=100, blank=True, null=True)
    escrow_released = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.applicant.username} - {self.task.title}"
    
    class Meta:
        unique_together = ['task', 'applicant']
        ordering = ['-created_at']

class ChatMessage(models.Model):
    """Chat messages between task creator and applicant"""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.sender.username} -> {self.receiver.username}: {self.content[:50]}"

class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('application', 'New Application'),
        ('application_accepted', 'Application Accepted'),
        ('application_rejected', 'Application Rejected'),
        ('task_completed', 'Task Completed'),
        ('message', 'New Message'),
        ('payment_proof', 'Payment Proof Uploaded'),
        ('payment_verified', 'Payment Verified'),
        ('escrow_funded', 'Escrow Funded'),
        ('payment_released', 'Payment Released'),
    ]
    
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_notifications', null=True, blank=True)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, null=True, blank=True)
    application = models.ForeignKey(Application, on_delete=models.CASCADE, null=True, blank=True)
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} for {self.recipient.username}"

class PaymentProof(models.Model):
    """Picture proof of payment for tasks"""
    STATUS_CHOICES = [
        ('pending', 'Pending Verification'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    ]
    
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='payment_proofs')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_payment_proofs')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_payment_proofs')
    image = models.ImageField(upload_to='payment_proofs/%Y/%m/%d/')
    caption = models.TextField(blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    verified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Payment proof for {self.task.title} - {self.amount}"

class TaskPaymentEscrow(models.Model):
    """Escrow system for task payments"""
    STATUS_CHOICES = [
        ('pending', 'Pending Payment'),
        ('funded', 'Funded'),
        ('released', 'Released to Worker'),
        ('refunded', 'Refunded to Poster'),
        ('disputed', 'Under Dispute'),
    ]
    
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name='escrow')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    poster_wallet = models.ForeignKey('wallet_app.Wallet', on_delete=models.CASCADE, related_name='poster_escrows')
    worker_wallet = models.ForeignKey('wallet_app.Wallet', on_delete=models.CASCADE, related_name='worker_escrows', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_proof = models.ForeignKey(PaymentProof, on_delete=models.SET_NULL, null=True, blank=True)
    funded_at = models.DateTimeField(blank=True, null=True)
    released_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Escrow for {self.task.title} - {self.amount}"

class EscrowRelease(models.Model):
    """Track how escrow is distributed among multiple workers"""
    escrow = models.ForeignKey(TaskPaymentEscrow, on_delete=models.CASCADE, related_name='releases')
    wallet = models.ForeignKey('wallet_app.Wallet', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    released_at = models.DateTimeField(auto_now_add=True)
    completion_key_used = models.CharField(max_length=100)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    def __str__(self):
        return f"Release for {self.escrow.task.title} - ₦{self.amount}"