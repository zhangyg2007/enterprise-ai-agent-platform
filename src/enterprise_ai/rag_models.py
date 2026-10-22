"""Local-only dense/reranker adapters and an explicitly synthetic test encoder."""
import hashlib
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .document_pipeline import _no_links, _digest

DIMENSION = 1024
ENCODER_REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
RERANKER_REVISION = '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'


def tokens(text):
    result=[]
    for word in re.findall(r'[\u3400-\u9fff]+|[a-z0-9]+',unicodedata.normalize('NFC',text).lower()):
        if '\u3400'<=word[0]<='\u9fff':
            result.extend(word)
            result.extend(word[i:i+2] for i in range(len(word)-1))
        else:
            result.append(word)
    return result


def vectors(rows,count):
    if not isinstance(rows,list) or len(rows)!=count:
        raise ValueError('embedding row count mismatch')
    for row in rows:
        if (not isinstance(row,list) or len(row)!=DIMENSION
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in row)):
            raise ValueError('invalid embedding dimension/value')
        norm=math.sqrt(sum(v*v for v in row))
        if not math.isfinite(norm) or abs(norm-1)>1e-4:
            raise ValueError('embedding must be nonzero and L2 normalized')
    return rows


class FixtureEncoder:
    def __init__(self):
        self.identity={'kind':'synthetic-fixture','model':'hash-bigrams','revision':'v1',
                       'dimension':DIMENSION,'pooling':'hashed-counts','normalized':True,'max_tokens':None}

    def encode(self,texts):
        result=[]
        for text in texts:
            row=[0.0]*DIMENSION
            for token,count in Counter(tokens(text) or ['<empty>']).items():
                pos=int.from_bytes(hashlib.sha256(token.encode()).digest()[:4],'big')%DIMENSION
                row[pos]+=count
            norm=math.sqrt(sum(v*v for v in row))
            result.append([v/norm for v in row])
        return vectors(result,len(texts))

    def verify(self):
        return None


def asset_digest(path):
    """Bind the supported single-file loader layout; never accept mixed weights."""
    path=_no_links(path).resolve(strict=True)
    weights=[n for n in ('model.safetensors','pytorch_model.bin') if (path/n).exists()]
    if len(weights)!=1 or list(path.glob('*.index.json')):
        raise ValueError('expected exactly one single-file model weight layout')
    required={'config.json','tokenizer.json','tokenizer_config.json',weights[0]}
    optional={'special_tokens_map.json','sentencepiece.bpe.model','added_tokens.json','generation_config.json'}
    hashes={}
    for name in sorted(required | {n for n in optional if (path/n).exists()}):
        item=_no_links(path/name)
        if not item.is_file(): raise ValueError('missing model asset: '+name)
        digest=hashlib.sha256(); size=0
        with item.open('rb') as stream:
            while raw:=stream.read(1024*1024): digest.update(raw); size+=len(raw)
        if not size: raise ValueError('empty model asset')
        hashes[name]={'sha256':digest.hexdigest(),'size':size}
    return hashes


class _LocalModel:
    def __init__(self,path,revision,*,reranker=False,device='cpu'):
        expected=RERANKER_REVISION if reranker else ENCODER_REVISION
        if revision!=expected or device not in ('cpu','cuda'):
            raise ValueError('use pinned revision and cpu/cuda device')
        self.path=Path(path); self.assets=asset_digest(path)
        import torch
        import transformers
        from transformers import AutoTokenizer, AutoModel, AutoModelForSequenceClassification
        self.torch=torch
        self.tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True,trust_remote_code=False)
        cls=AutoModelForSequenceClassification if reranker else AutoModel
        self.model=cls.from_pretrained(str(path),local_files_only=True,trust_remote_code=False,
                                      weights_only=True,use_safetensors='model.safetensors' in self.assets,
                                      torch_dtype=torch.float32).to(device).eval()
        if self.model.config.model_type!='xlm-roberta' or self.model.config.hidden_size!=DIMENSION:
            raise ValueError('expected BGE-M3 XLM-R 1024 architecture')
        if reranker and self.model.config.num_labels!=1:
            raise ValueError('expected single-logit reranker')
        self.device=device
        self.identity={'kind':'local-transformers','model':'BAAI/bge-reranker-v2-m3' if reranker else 'BAAI/bge-m3',
                       'revision':revision,'assets':self.assets,'dimension':1 if reranker else DIMENSION,
                       'pooling':'sigmoid-logit' if reranker else 'cls','normalized':not reranker,
                       'max_tokens':1024,'dtype':'float32','device':device,
                       'torch':torch.__version__,'transformers':transformers.__version__}
        self.verify()

    def verify(self):
        if asset_digest(self.path)!=self.assets:
            raise ValueError('local model assets changed')

    def _inputs(self,texts):
        encoded=self.tokenizer(texts,padding=True,truncation=False,return_tensors='pt')
        if encoded['input_ids'].shape[1]>1024:
            raise ValueError('input exceeds 1024 tokens; shorten chunks/query explicitly')
        return {key:value.to(self.device) for key,value in encoded.items()}


class LocalEncoder(_LocalModel):
    def __init__(self,path,revision=ENCODER_REVISION,device='cpu'):
        super().__init__(path,revision,device=device)

    def encode(self,texts):
        rows=[]
        with self.torch.inference_mode():
            for start in range(0,len(texts),4):
                output=self.model(**self._inputs(texts[start:start+4])).last_hidden_state[:,0]
                rows.extend(self.torch.nn.functional.normalize(output,p=2,dim=1).cpu().tolist())
        self.verify()
        return vectors(rows,len(texts))


class LocalReranker(_LocalModel):
    def __init__(self,path,revision=RERANKER_REVISION,device='cpu'):
        super().__init__(path,revision,reranker=True,device=device)

    def score(self,query,texts):
        scores=[]
        with self.torch.inference_mode():
            for start in range(0,len(texts),4):
                pairs=[[query,text] for text in texts[start:start+4]]
                values=self.model(**self._inputs(pairs)).logits.view(-1).float()
                scores.extend(self.torch.sigmoid(values).cpu().tolist())
        self.verify()
        return scores


def encoder_options(parser):
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--fixture',action='store_true')
    group.add_argument('--model',type=Path)
    parser.add_argument('--device',choices=('cpu','cuda'),default='cpu')


def make_encoder(args):
    return FixtureEncoder() if args.fixture else LocalEncoder(args.model,device=args.device)
