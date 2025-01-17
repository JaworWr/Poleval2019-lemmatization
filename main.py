import conllu_loader as cl
from models import BiLSTM_CRF_extra
from tokenizer import Tokenizer, sentences_to_list, substitute_forms
from word_transformation import application
import argparse
import sys
import torch
from morf_tools import get_all_tags

def split_genders(xs):
    res = set()
    for n, g in xs:
        gs = g.split('.')
        for gg in gs:
            res.add((n, gg))
    return res

def rule_tags(words):
    gt = [get_all_tags(w) for w in words]
    tags = [None] * len(gt)
    subst_ind = -1
    for i, t in enumerate(gt):
        if all(a[0] == 'subst' for a in t):
            if subst_ind >= 0:
                return tags
            common = set((a[1], a[-1]) for a in t)
            common = split_genders(common)
            subst_ind = i
        elif not all(a[0] == 'adj' for a in t):
            return tags
    if subst_ind >= 0:
        for t in gt:
            if all(a[0] == 'adj' for a in t):
                s = set((a[1], a[3]) for a in t)
                s = split_genders(s)
                common &= s
        common = list(common)
        if len(common) > 0:
            for i in range(len(words)):
                if i == subst_ind:
                    tags[i] = 'subst:{}:{}'.format(*common[0])
                else:
                    tags[i] = 'adj:nom:{}:{}'.format(*common[0])
    return tags

def fixing(tags):
    gt = [t.split(':') for t in tags]
    c = 0
    for t in gt:
        if t[0] == 'subst':
            c += 1
            g = t[-1]
    if c == 1:
        for t in gt:
            if t[0] == 'adj':
                t[-1] = g
        tags = [':'.join(t) for t in gt]
    return tags

def apply_tags(oc, words, ops):
    r = [application(w, op) for w, op in zip(words, ops)]
    r1 = []
    for word, case in zip(r, oc):
        if case == 'c':
            word = word[0].upper() + word[1:]
        elif case == 'al-':
            word = word[:3] + word[3].upper() + word[4:]
        elif case == 'l':
            word = word.lower()
        r1.append(word)
    for i, w in enumerate(r1):
        if w in ['?', '<guessed-form>']:
            r1[i] = words[i]
    return r1

def run_tests(loader, model, tok, wiki_phrases=dict()):
    results = []

    model.eval()
    with torch.no_grad():
        for p, data, extras, bounds, text in loader.iter_phrases():
            phrase, lemma, old_tag = p[['phrase', 'lemma', 'tag']]
            if phrase in wiki_phrases:
                result = wiki_phrases[phrase]
                tag = None
            else:
                forms = cl.text_property(text, 'form')[bounds[0]:bounds[1]]
                lemmas = cl.text_property(text, 'lemma')[bounds[0]:bounds[1]]
                pos = cl.text_property(text, 'upostag')[bounds[0]:bounds[1]]
                tag = rule_tags(forms)
                if tag[0] is None:
                    tag = model(data, extras, bounds)
                    tag = [cl.ix_to_tag[i] for i in tag.flatten()]
                    tag = fixing(tag)
                lemmas = cl.text_property(text, 'lemma')[bounds[0]:bounds[1]]
                pos = cl.text_property(text, 'upostag')[bounds[0]:bounds[1]]

                ps = tok.tokenize(phrase)
                tokens = sentences_to_list(ps)
                if len(tokens) == 1 and pos[0] == 'ADJ':
                    oc = ['l']
                else:
                    oc = []
                    for token in tokens:
                        if token[0].isupper():
                            oc.append('c')
                        elif token.startswith('al-') and token[3].isupper():
                            oc.append('al-')
                        elif token[0].islower():
                            oc.append('l')
                        else:
                            oc.append('?')
                result = apply_tags(oc, tokens, tag)
                try:
                    substitute_forms(ps, result)
                except StopIteration:
                    pass
                result = tok.write(ps, 'plaintext')
            
            results.append((phrase, lemma, result, old_tag, tag, p))
    return results

def main(args):
    if torch.cuda.is_available():
        device = 'cuda'
        print("Using device '{}'.".format(torch.cuda.get_device_name(None)), file=sys.stderr)
    else:
        device = 'cpu'
        print("CUDA is not available. Using CPU.", file=sys.stderr)

    model = torch.load(args['model'], map_location=device)
    tok = Tokenizer(args['tokenizer_model'])

    cl.load_dicts(model['dicts'])
    data_loader = cl.ConlluLoader(args['data'], args['index'], model.get('suffixes', None), device=device, update_freqs=False, ignore_tags=True)
    data_loader.prepare_data(**model['preparation_params'], update_dicts=False)

    wiki_phrases = dict()
    with open('data/wiki_lemmatization.txt') as file:
        for line in file:
            phrase, lemma = line.strip().split(' --- ')
            wiki_phrases[phrase] = lemma
    
    net = BiLSTM_CRF_extra(256, 200, 1024, len(cl.word_to_ix), len(cl.extra_to_ix), len(cl.tag_to_ix), device=device)
    net.load_state_dict(model['net'])
    net.eval()
    results = run_tests(data_loader, net, tok, wiki_phrases)

    with open(args['output'], 'w') as file:
        for phrase, lemma, result, old_tag, tag, p in results:
            line = '\t'.join([str(p.name), p['document_id'], phrase, result])
            file.write(line + '\n')
    
    print("Done.", file=sys.stderr)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', help='path to model', type=str, required=True)
    parser.add_argument('--tokenizer_model', help='path to tokenizer model', type=str, required=True)
    parser.add_argument('--data', help='path to data in the CoNLL-U format', type=str, required=True)
    parser.add_argument('--index', help='path to data index', type=str, required=True)
    parser.add_argument('--output', help='output path', type=str, required=True)
    args = vars(parser.parse_args())
    main(args)
