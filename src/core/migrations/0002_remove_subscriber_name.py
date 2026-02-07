# Generated migration to remove name field from Subscriber model

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='subscriber',
            name='name',
        ),
    ]
