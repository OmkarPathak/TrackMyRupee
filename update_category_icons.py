import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finance_tracker.settings')
django.setup()

from expenses.models import Category

NEW_ICONS = {
    'Clothing': 'bi-handbag',
    'Shopping': 'bi-shop',
    'Groceries': 'bi-upc-scan',
    'Vegetables': 'bi-basket',
    'Fuel': 'bi-fuel-pump',
    'Jewellery': 'bi-gem',
    'Insurance': 'bi-tsunami',
    'Medicines': 'bi-capsule',
}

def update_icons():
    count = 0
    for name, icon in NEW_ICONS.items():
        # Update categories that have the name but a different icon (usually bi-tag)
        updated = Category.objects.filter(name__iexact=name).update(icon=icon)
        count += updated
        if updated:
            print(f"Updated {updated} categories for '{name}' to use icon '{icon}'")
    
    print(f"\nTotal categories updated: {count}")

if __name__ == "__main__":
    update_icons()
