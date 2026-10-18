import tempfile,unittest
from pathlib import Path
import projects as P

class FullRead(unittest.TestCase):
 def test_all_csv_rows_and_long_text_are_read_on_request(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);csv=root/'数据.csv';csv.write_text('id,value\n'+''.join(f'{i},row-{i}\n' for i in range(650)),encoding='utf-8')
   self.assertTrue(P.preview(csv)['truncated']);r=P.preview(csv,full=True);self.assertTrue(r['full_read']);self.assertEqual(len(r['sheets'][0]['rows']),651);self.assertEqual(r['sheets'][0]['rows'][-1][1],'row-649')
   text=root/'长文.txt';text.write_text('内容'*120000+'TAIL_MARKER',encoding='utf-8');self.assertTrue(P.preview(text)['truncated']);self.assertTrue(P.preview(text,full=True)['text'].endswith('TAIL_MARKER'))
 def test_all_excel_sheets_and_word_headers_are_read_and_handles_close(self):
  try:from openpyxl import Workbook;from docx import Document
  except ImportError:self.skipTest('Office parser dependencies supplied in Windows portable runtime')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);p=root/'多表.xlsx';w=Workbook()
   for i in range(10):
    s=w.active if i==0 else w.create_sheet();s.title='sheet'+str(i);s.append(['实际内容',i])
   w.active.append(['公式','=1+2']);w.save(p);w.close();self.assertTrue(P.preview(p)['truncated']);r=P.preview(p,full=True);self.assertEqual(len(r['sheets']),10);self.assertEqual(r['sheets'][0]['rows'][1][1],'=1+2');p.unlink()
   d=Document();d.add_paragraph('正文');d.sections[0].header.paragraphs[0].text='HEADER_MARKER';d.sections[0].footer.paragraphs[0].text='FOOTER_MARKER';p=root/'完整文档.docx';d.save(p)
   r=P.preview(p,full=True);self.assertIn('HEADER_MARKER',r['text']);self.assertIn('FOOTER_MARKER',r['text']);p.unlink()

if __name__=='__main__':unittest.main(verbosity=2)
