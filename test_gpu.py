import unittest
from unittest.mock import patch
import hub_core
import hub_gpu

class GpuTests(unittest.TestCase):
    def test_ollama_gpu_allocation_failure_is_not_cpu_only(self):
        text='load_backend: loaded CPU backend\nload_backend: loaded ROCm backend\nDevice 0: AMD Radeon 8060S\nggml_backend_cuda_buffer_type_alloc_buffer: allocating 49171.42 MiB on device 0: cudaMalloc failed: out of memory'
        facts=hub_gpu.parse_runtime_log(text,'Ollama')
        self.assertTrue(facts['gpu_seen']);self.assertEqual(facts['backend'],'ROCm')
        self.assertAlmostEqual(facts['failed_gpu_allocation_gib'],48.0189648,places=3)

    def test_strata_ram_guard_does_not_erase_gpu_evidence(self):
        text='strata generate: memory guard: limit 31.6 GiB (source: meminfo)\nstrata generate: ExpertCache: cudaMalloc(56.24 GiB) failed: out of memory (Windows has 45.3 GiB of commit left: RAM + page file); trying a smaller expert cache: 18448 slots\nstrata serve: 35213 MiB of VRAM free with everything loaded'
        facts=hub_gpu.parse_runtime_log(text,'Strata')
        self.assertTrue(facts['gpu_seen']);self.assertEqual(facts['system_ram_guard_gib'],31.6)
        self.assertEqual(facts['failed_gpu_allocation_gib'],56.24);self.assertEqual(facts['commit_free_at_failure_gib'],45.3)
        explanation=hub_gpu.explanation([facts])[0]
        self.assertIn('not a cap on graphics memory',explanation);self.assertIn('not proof',explanation)

    def test_loading_cpu_library_alone_does_not_prove_cpu_fallback(self):
        facts=hub_gpu.parse_runtime_log('load_backend: loaded CPU backend','Ollama')
        self.assertFalse(facts['gpu_seen']);self.assertNotIn('cpu_only',facts)

    def test_parser_does_not_copy_secret_log_text(self):
        text='SECRET_TOKEN=private\nload_backend: loaded Vulkan backend from C:/private/path\nprompt=private words'
        self.assertNotIn('private',str(hub_gpu.parse_runtime_log(text,'Ollama')))

    def test_live_gpu_check_distinguishes_missing_and_present_allocations(self):
        with patch.object(hub_core,'api',return_value={'models':[{'name':'cpu','size':100,'size_vram':0},{'name':'gpu','size':100,'size_vram':100}]}):
            result=hub_gpu.live_ollama_usage('http://127.0.0.1:11434')
        self.assertEqual(result['models'][0]['state'],'no graphics memory reported')
        self.assertEqual(result['models'][1]['state'],'graphics memory in use')

if __name__=='__main__':unittest.main(verbosity=2)
