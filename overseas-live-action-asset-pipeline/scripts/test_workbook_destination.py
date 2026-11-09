from pathlib import Path
import tempfile
import unittest
from input_check import validate_request
from workbook_data import build_payload
import test_pipeline as fixtures


class WorkbookDestinationTests(unittest.TestCase):
    def test_script_parent_default_does_not_enable_image_archiving(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder)/'剧本.txt'
            script.write_text('EPISODE 1\nEmma enters.',encoding='utf-8')
            raw = {'region_mode':'domestic','script_path':str(script)}
            result = validate_request(raw)
            self.assertEqual(result['workbook_output_dir'],str(script.parent.resolve()))
            self.assertIsNone(result['output_root'])
            self.assertFalse(result['save_assets'])
            self.assertFalse(result['generate_images'])
            target = str(Path(folder)/'明确交付目录')
            self.assertEqual(validate_request({**raw,'workbook_output_dir':target})['workbook_output_dir'],target)
            self.assertEqual(validate_request({**raw,'output_root':target})['workbook_output_dir'],str(script.parent.resolve()))
            self.assertFalse(Path(target).exists())
            self.assertIsNone(validate_request({**raw,'output_mode':'prompts'})['workbook_output_dir'])

    def test_payload_uses_script_directory_and_text_falls_back_to_state_workspace(self):
        case = fixtures.PipelineTests()
        case.setUp()
        try:
            case.init(output_mode='workbook')
            self.assertEqual(build_payload(case.state_path)['workbook_output_dir'],str(case.state_path.parent))
            source_dir=case.folder/'source'
            source_dir.mkdir()
            script=source_dir/'剧本.txt'
            script.write_text(fixtures.SCRIPT,encoding='utf-8')
            case.raw.pop('script_text')
            case.raw['script_path']=str(script)
            from pipeline_common import script_hash
            case.annotations['source_sha256']=script_hash(case.raw)
            case.init(output_mode='workbook')
            self.assertEqual(build_payload(case.state_path)['workbook_output_dir'],str(source_dir))
            self.assertFalse((source_dir/'人物').exists())
        finally:
            case.doCleanups()


if __name__=='__main__':
    unittest.main()
