from django.db import migrations, models
from django.db.models import Sum

def populate_escrow_balance(apps, schema_editor):
    Campaign = apps.get_model('fundraising_app', 'Campaign')
    Contribution = apps.get_model('fundraising_app', 'Contribution')
    
    for campaign in Campaign.objects.all():
        # Sum all paid contributions for this campaign
        total_contributions = Contribution.objects.filter(
            campaign=campaign,
            status='paid'
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Update escrow_balance with the total
        campaign.escrow_balance = total_contributions
        campaign.save()

def reverse_func(apps, schema_editor):
    # Reverse sets escrow_balance back to 0
    Campaign = apps.get_model('fundraising_app', 'Campaign')
    Campaign.objects.update(escrow_balance=0)

class Migration(migrations.Migration):
    dependencies = [
        ('fundraising_app', '0002_campaign_escrow_balance_campaign_is_withdrawn'),
    ]

    operations = [
        migrations.RunPython(populate_escrow_balance, reverse_func),
    ]