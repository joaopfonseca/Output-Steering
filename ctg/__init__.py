import numpy as np
import torch


class ModelWrapper(object):
    def __init__(
        self,
        model,
        tokenizer,
        mode="topk",
        k=100,
        p=0.9,
        temperature=1.0,
        random_state=None,
        cuda=False,
    ):
        self.model = model
        self.tokenizer = tokenizer
        self.mode = mode
        self.k = k
        self.p = p
        self.temperature = 1.0
        self.random_state = random_state
        self.cuda = cuda

        self._rng = np.random.default_rng(self.random_state)

    def get_hidden_state(self, prompt, response):
        """
        Helper function to get hidden states for a given prompt + response combination.
        """
        input = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response},
        ]

        input_ids = self._get_ids(input)

        _, last_hidden_state = self._forward_pass_from_ids(input_ids)
        last_hidden_state = last_hidden_state[0, -1]
        return last_hidden_state

    def generate(self, prompt, target="", max_tokens=100, verbose=False):

        input = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": target},
        ]

        input_ids = self._get_ids(input)
        sentence = target

        if verbose:
            print(sentence, end="")

        j = 0
        while True:

            logits_top, logits_top_idx = self.get_top_logits_from_ids(input_ids)
            probs_top = torch.nn.functional.softmax(
                logits_top / self.temperature, dim=-1
            )
            next_token = logits_top_idx[
                self._rng.choice(len(logits_top_idx), p=probs_top.detach().numpy())
            ]

            j += 1
            if (next_token.item() == self.tokenizer.eos_token_id) or (j > max_tokens):
                _, last_hidden_state = self._forward_pass_from_ids(input_ids)
                if verbose:
                    print("\n")
                return sentence, last_hidden_state

            if self.tokenizer.name_or_path.find("mistral") > -1:
                next_token_str = self.tokenizer.convert_ids_to_tokens(
                    next_token.item()
                ).replace("▁", " ")
            else:
                next_token_str = self.tokenizer.decode(next_token.item())

            sentence += next_token_str
            if verbose:
                print(next_token_str, end="")

            input_ids = torch.cat((input_ids, next_token.reshape(1, 1)), dim=1)

    def get_top_logits_from_ids(self, input_ids):
        logits, last_hidden_state = self._forward_pass_from_ids(input_ids)
        logits = logits[-1, -1]

        if self.mode is None:
            logits_top, logits_top_idx = torch.topk(logits, len(logits))

        if self.mode == "topk":
            logits_top, logits_top_idx = torch.topk(logits, self.k)

        if self.mode == "topp":
            # Need to implement
            return False

        return logits_top, logits_top_idx

    def _get_ids(self, input):
        input_ids = self.tokenizer.apply_chat_template(input, return_tensors="pt")
        input_ids = input_ids[0][:-1].reshape(1, -1)

        return input_ids

    def _forward_pass_from_ids(self, input_ids):
        if self.cuda:
            input_ids = input_ids.cuda()

        with torch.no_grad():
            outputs = self.model(
                input_ids,
                use_cache=False,
                output_hidden_states=True,
                output_attentions=False,
            )
            logits = outputs["logits"]
            last_hidden_state = outputs["hidden_states"][-1]
            del outputs
            del input_ids

        if self.cuda:
            logits = logits.cpu()
            last_hidden_state = last_hidden_state.cpu()

        return logits, last_hidden_state


class CTG(ModelWrapper):

    def generate(
        self, prompt, embedder, clf, target="", tau=0.5, max_tokens=100, verbose=False
    ):

        input = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": target},
        ]

        input_ids = self._get_ids(input)
        sentence = target

        if verbose:
            print(sentence, end="")

        j = 0
        while True:

            logits_top, logits_top_idx = self.get_top_logits_from_ids(input_ids)

            mask = np.ones(len(logits_top_idx))

            scores = []
            for i, idx in enumerate(logits_top_idx):
                if self.tokenizer.name_or_path.find("mistral") > -1:
                    next_token_str = self.tokenizer.convert_ids_to_tokens(
                        idx.item()
                    ).replace("▁", " ")
                else:
                    next_token_str = self.tokenizer.decode(idx.item())
                # print(sentence+next_token_str)
                embedding = embedder.encode(sentence + next_token_str)
                score = clf.predict_proba([embedding])[0][1]
                scores.append(score)
                if score > tau:
                    mask[i] = 0
            # print(scores)

            mask = torch.from_numpy(np.array(mask, dtype="bool"))
            logits_top = torch.masked_select(logits_top, mask)
            logits_top_idx = torch.masked_select(logits_top_idx, mask)

            probs_top = torch.nn.functional.softmax(
                logits_top / self.temperature, dim=-1
            )
            next_token = logits_top_idx[
                self._rng.choice(len(logits_top_idx), p=probs_top.detach().numpy())
            ]

            j += 1
            if (next_token.item() == self.tokenizer.eos_token_id) or (j > max_tokens):
                _, last_hidden_state = self._forward_pass_from_ids(input_ids)
                if verbose:
                    print("\n")
                return sentence, last_hidden_state

            if self.tokenizer.name_or_path.find("mistral") > -1:
                next_token_str = self.tokenizer.convert_ids_to_tokens(
                    next_token.item()
                ).replace("▁", " ")
            else:
                next_token_str = self.tokenizer.decode(next_token.item())

            sentence += next_token_str
            if verbose:
                print(next_token_str, end="")

            input_ids = torch.cat((input_ids, next_token.reshape(1, 1)), dim=1)