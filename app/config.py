import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

SERVICES_TABLE = os.getenv("SERVICES_TABLE", "purezen_services")
AVAILABILITY_TABLE = os.getenv("AVAILABILITY_TABLE", "purezen_availability")
BOOKINGS_TABLE = os.getenv("BOOKINGS_TABLE", "purezen_bookings")
