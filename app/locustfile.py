from locust import HttpUser, task, between

class PureZenUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def check_availability(self):
        self.client.post("/chat", json={
            "message": "Do you have availability for a Swedish Massage tomorrow?",
            "session_id": f"test-{self.user_id}"
        })

    @task(1)
    def list_services(self):
        self.client.get("/services")
