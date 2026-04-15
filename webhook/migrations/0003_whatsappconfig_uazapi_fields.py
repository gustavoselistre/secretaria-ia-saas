from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('webhook', '0002_alter_phone_max_length'),
    ]

    operations = [
        migrations.RenameField(
            model_name='whatsappconfig',
            old_name='twilio_phone_number',
            new_name='phone_number',
        ),
        migrations.AlterField(
            model_name='whatsappconfig',
            name='phone_number',
            field=models.CharField(
                help_text=(
                    "Número WhatsApp do bot. Twilio: 'whatsapp:+5551999990000'. "
                    "uazapi: '5551999990000'."
                ),
                max_length=30,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='whatsappconfig',
            name='uazapi_instance_id',
            field=models.CharField(
                blank=True,
                help_text='ID da instância uazapi (vazio quando o provider é Twilio).',
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='whatsappconfig',
            name='uazapi_instance_token',
            field=models.CharField(
                blank=True,
                help_text='Token de autenticação da instância uazapi.',
                max_length=255,
                null=True,
            ),
        ),
    ]
