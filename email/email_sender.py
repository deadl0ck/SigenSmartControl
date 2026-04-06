import smtplib
import ssl


class EmailSender:
    def __init__(self, sender_gmail: str, gmail_app_password: str):
        self.sender_gmail = sender_gmail
        self.password = gmail_app_password

    def send(self, receiver_email: str, subject: str, text: str):
        port = 465  # For SSL
        smtp_server = "smtp.gmail.com"
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
            server.login(self.sender_gmail, self.password)
            message = f'Subject: {subject}\n\n{text}'
            server.sendmail(self.sender_gmail, receiver_email, message)

