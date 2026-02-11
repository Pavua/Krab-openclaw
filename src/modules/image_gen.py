import aiohttp
import asyncio
import json
import uuid
import os
import random
from typing import Optional, Dict, Any, List

class ComfyClient:
    def __init__(self, host="127.0.0.1", port=8192):
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"
        self.client_id = str(uuid.uuid4())

    async def check_health(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/system_stats") as resp:
                    return resp.status == 200
        except:
            return False

    async def queue_prompt(self, prompt_workflow: Dict[str, Any]) -> str:
        async with aiohttp.ClientSession() as session:
            payload = {"prompt": prompt_workflow, "client_id": self.client_id}
            async with session.post(f"{self.base_url}/prompt", json=payload) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to queue prompt: {await resp.text()}")
                data = await resp.json()
                return data['prompt_id']

    async def get_history(self, prompt_id: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/history/{prompt_id}") as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()

    async def get_image(self, filename: str, subfolder: str, folder_type: str) -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/view", params=params) as resp:
                if resp.status == 200:
                    return await resp.read()
                return None

class FluxWorkflow:
    @staticmethod
    def get_workflow(prompt: str, seed: int = None) -> Dict[str, Any]:
        if seed is None:
            seed = random.randint(1, 999999999999999)
            
        # Basic Flux GGUF Workflow
        # Node IDs are arbitrary but must link correctly
        workflow = {
            "10": {
                "inputs": {
                    "unet_name": "flux1-dev-Q8_0.gguf"
                },
                "class_type": "UnetLoaderGGUF",
                "_meta": {"title": "Unet Loader (GGUF)"}
            },
            "11": {
                "inputs": {
                    "clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
                    "clip_name2": "clip_l.safetensors",
                    "type": "flux"
                },
                "class_type": "DualCLIPLoaderGGUF", 
                "_meta": {"title": "DualCLIPLoader (GGUF)"}
            },
            "12": {
                "inputs": {
                    "vae_name": "ae.safetensors"
                },
                "class_type": "VAELoader",
                "_meta": {"title": "Load VAE"}
            },
            "6": {
                "inputs": {
                    "text": prompt,
                    "clip": ["11", 0]
                },
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode (Positive)"}
            },
            "7": {
                "inputs": {
                    "text": "",
                    "clip": ["11", 0]
                },
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode (Negative)"}
            },
            "8": {
                "inputs": {
                    "width": 1024,
                    "height": 1024,
                    "batch_size": 1
                },
                "class_type": "EmptyLatentImage",
                "_meta": {"title": "Empty Latent Image"}
            },
            "13": { # Sampler
                "inputs": {
                    "seed": seed,
                    "steps": 20,
                    "cfg": 1.0, 
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["10", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["8", 0]
                },
                "class_type": "KSampler",
                "_meta": {"title": "KSampler"}
            },
            "9": {
                "inputs": {
                    "samples": ["13", 0],
                    "vae": ["12", 0]
                },
                "class_type": "VAEDecode",
                "_meta": {"title": "VAE Decode"}
            },
            "14": {
                "inputs": {
                    "filename_prefix": "Krab_Flux",
                    "images": ["9", 0]
                },
                "class_type": "SaveImage",
                "_meta": {"title": "Save Image"}
            }
        }
        return workflow

class ImageGenerator:
    def __init__(self):
        self.client = ComfyClient()
        
    async def generate(self, prompt: str) -> Optional[bytes]:
        try:
            if not await self.client.check_health():
                print("ComfyUI is offline")
                return None
            
            workflow = FluxWorkflow.get_workflow(prompt)
            prompt_id = await self.client.queue_prompt(workflow)
            
            # Simple polling (max 400s)
            for _ in range(200): # 200 * 2 = 400s
                await asyncio.sleep(2)
                history = await self.client.get_history(prompt_id)
                if prompt_id in history:
                    outputs = history[prompt_id]['outputs']
                    for node_id in outputs:
                        if 'images' in outputs[node_id]:
                            img_info = outputs[node_id]['images'][0]
                            return await self.client.get_image(
                                img_info['filename'], 
                                img_info['subfolder'], 
                                img_info['type']
                            )
            return None
        except Exception as e:
            print(f"Generation failed: {e}")
            return None
