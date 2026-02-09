import os
import logging
from pathlib import Path

logger = logging.getLogger("Nexus.ModelScanner")

class ModelScanner:
    def __init__(self):
        self.lm_studio_paths = [
            Path.home() / ".cache/lm-studio/models",
            Path.home() / ".lmstudio/models"
        ]
        
    def scan_models(self):
        """
        Scans common LM Studio directories for model files.
        Returns a list of dicts: {'id': 'author/name', 'path': ...}
        """
        found_models = []
        
        for base_path in self.lm_studio_paths:
            if not base_path.exists():
                continue
                
            # LM Studio structure is usually author/repo/file.gguf
            for author_dir in base_path.iterdir():
                if author_dir.is_dir():
                    for repo_dir in author_dir.iterdir():
                        if repo_dir.is_dir():
                            for file in repo_dir.glob("*.gguf"):
                                model_id = f"{author_dir.name}/{repo_dir.name}/{file.name}"
                                found_models.append({
                                    "id": model_id,
                                    "name": file.stem,
                                    "path": str(file)
                                })
        
        logger.info(f"🔎 Scanned {len(found_models)} local models.")
        return found_models

    def list_models_formatted(self):
        models = self.scan_models()
        if not models:
            return "No local models found in standard LM Studio paths."
        
        output = "**Available Local Models:**\n"
        for m in models:
            output += f"- `{m['name']}`\n"
        return output
