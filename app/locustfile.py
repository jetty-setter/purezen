cat << 'EOF' > locustfile.py
from locust import HttpUser, task, between
import random
import string

class PureZenUser(HttpUser):
    wait_time = between(1, 3)

    def random_session(self):
        return ''.join(random.choices(string.ascii_lowercase, k=8))

    @task(3)
    def check_availability(self):
        self.client.post("/chat", json={
            "message": "Do you have availability for a Swedish Massage tomorrow?",
            "session_id": self.random_session()
        })

    @task(1)
    def list_services(self):
        self.client.get("/services")
EOF

