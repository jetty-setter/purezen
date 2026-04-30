from locust import HttpUser, task, between
import random

class PureZenUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def health(self):
        self.client.get("/health")

    @task(3)
    def services(self):
        self.client.get("/services")

    @task(2)
    def bookings(self):
        self.client.get("/bookings/history?email=ckoch@example.com")

    @task(1)
    def chat(self):
        self.client.post("/chat", json={
            "message": "What massages do you offer?",
            "session_id": f"load-test-{random.randint(1,100)}"
        })
