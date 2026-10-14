import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("HF_TOKEN")
print("token loaded:", bool(token))

from huggingface_hub import InferenceClient

client = InferenceClient(api_key=token)
img = client.text_to_image(
    "clean abstract illustration of glowing connected blue nodes",
    model="black-forest-labs/FLUX.1-schnell",
)
img.save("test.png")
print("saved test.png")