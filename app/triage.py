"""Small deterministic file checks; no challenge-specific names or answers."""
import json, subprocess, sys
from pathlib import Path

def decode_bits(source, output):
    carry=b''; count=0
    try:
        with source.open('rb') as src, output.open('wb') as dst:
            while chunk:=src.read(65536):
                bits=carry+b''.join(chunk.split())
                if bits.translate(None,b'01'): raise ValueError('Not binary text')
                end=len(bits)//8*8
                dst.write(bytes(int(bits[i:i+8],2) for i in range(0,end,8)))
                count+=end//8; carry=bits[end:]
            if carry or not count: raise ValueError('Bit count must be divisible by eight')
        return count
    except Exception:
        output.unlink(missing_ok=True); raise

def inspect_image(path):
    from PIL import Image,ImageChops
    with Image.open(path) as im:
        if im.width*im.height>20_000_000: return {'image':'Too large for automatic OCR'}
        im=im.convert('RGB')
        gray=im.convert('L'); mask=gray.point(lambda p:255 if p<220 else 0)
        box=mask.getbbox()
        if box: im=im.crop(box)
        factor=min(4, max(1,2000//max(1,im.width)))
        im=im.resize((im.width*factor,im.height*factor))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png') as tmp:
            im.save(tmp.name)
            r=subprocess.run(['tesseract',tmp.name,'stdout','--psm','6'],capture_output=True,text=True,timeout=15)
        return {'image':str(path),'ocr':r.stdout[:1500], 'note':'OCR is approximate; inspect the image to confirm exact flag characters.'}

def triage(folder):
    results=[]
    for source in sorted(folder.iterdir()):
        if not source.is_file() or source.is_symlink() or source.name.startswith('recovered-'): continue
        if len(results)>=20: break
        entry={'file':source.name}
        try:
            with source.open('rb') as f: sample=b''.join(f.read(4096).split())
            target=source
            if len(sample)>=16 and not sample.translate(None,b'01') and source.stat().st_size<=256*1024*1024:
                target=folder/('recovered-'+source.name+'.bin')
                entry['decoded_bytes']=decode_bits(source,target)
                entry['recovered_file']=target.name
            r=subprocess.run(['file','-b','--',str(target)],capture_output=True,text=True,timeout=5)
            entry['type']=r.stdout.strip()
            if any(x in entry['type'] for x in ('JPEG image','PNG image','GIF image','bitmap')):
                try: entry.update(inspect_image(target))
                except Exception as e: entry['ocr_error']=str(e)
        except Exception as e: entry['error']=str(e)
        results.append(entry)
    return results
if __name__=='__main__': print(json.dumps(triage(Path.cwd())))
