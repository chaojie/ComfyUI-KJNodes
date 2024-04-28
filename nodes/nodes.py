import torch
import torch.nn.functional as F
from torchvision.transforms import functional as TF

import scipy.ndimage
import matplotlib.pyplot as plt
import numpy as np
from PIL import ImageFilter, Image, ImageDraw, ImageFont
from contextlib import nullcontext

import json
import re
import os
import io
import random
import math

import model_management
from nodes import MAX_RESOLUTION, SaveImage, CLIPTextEncode
from comfy_extras.nodes_mask import ImageCompositeMasked
import comfy.sample
import folder_paths
from ..utility.utility import tensor2pil, pil2tensor

script_directory = os.path.dirname(os.path.abspath(__file__))
folder_paths.add_model_folder_path("kjnodes_fonts", os.path.join(script_directory, "fonts"))

class AnyType(str):
  """A special class that is always equal in not equal comparisons. Credit to pythongosssss"""

  def __ne__(self, __value: object) -> bool:
    return False
any = AnyType("*")

class INTConstant:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "value": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
        },
        }
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("value",)
    FUNCTION = "get_value"
    CATEGORY = "KJNodes/constants"

    def get_value(self, value):
        return (value,)

class FloatConstant:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "value": ("FLOAT", {"default": 0.0, "min": -0xffffffffffffffff, "max": 0xffffffffffffffff, "step": 0.001}),
        },
        }

    RETURN_TYPES = ("FLOAT",)
    RETURN_NAMES = ("value",)
    FUNCTION = "get_value"
    CATEGORY = "KJNodes/constants"

    def get_value(self, value):
        return (value,)

class StringConstant:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "string": ("STRING", {"default": '', "multiline": False}),
            }
        }
    RETURN_TYPES = ("STRING",)
    FUNCTION = "passtring"
    CATEGORY = "KJNodes/constants"

    def passtring(self, string):
        return (string, )

class StringConstantMultiline:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "string": ("STRING", {"default": "", "multiline": True}),
                "strip_newlines": ("BOOLEAN", {"default": True}),
            }
        }
    RETURN_TYPES = ("STRING",)
    FUNCTION = "stringify"
    CATEGORY = "KJNodes/constants"

    def stringify(self, string, strip_newlines):
        new_string = []
        for line in io.StringIO(string):
            if not line.strip().startswith("\n") and strip_newlines:
                line = line.replace("\n", '')
            new_string.append(line)
        new_string = "\n".join(new_string)

        return (new_string, )
    
class JoinStrings:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "string1": ("STRING", {"default": '', "forceInput": True}),
                "string2": ("STRING", {"default": '', "forceInput": True}),
                "delimiter": ("STRING", {"default": ' ', "multiline": False}),
            }
        }
    RETURN_TYPES = ("STRING",)
    FUNCTION = "joinstring"
    CATEGORY = "KJNodes/constants"

    def joinstring(self, string1, string2, delimiter):
        joined_string = string1 + delimiter + string2
        return (joined_string, )

class CreateFluidMask:
    
    RETURN_TYPES = ("IMAGE", "MASK")
    FUNCTION = "createfluidmask"
    CATEGORY = "KJNodes/masking/generate"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "invert": ("BOOLEAN", {"default": False}),
                 "frames": ("INT", {"default": 0,"min": 0, "max": 255, "step": 1}),
                 "width": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
                 "inflow_count": ("INT", {"default": 3,"min": 0, "max": 255, "step": 1}),
                 "inflow_velocity": ("INT", {"default": 1,"min": 0, "max": 255, "step": 1}),
                 "inflow_radius": ("INT", {"default": 8,"min": 0, "max": 255, "step": 1}),
                 "inflow_padding": ("INT", {"default": 50,"min": 0, "max": 255, "step": 1}),
                 "inflow_duration": ("INT", {"default": 60,"min": 0, "max": 255, "step": 1}),
        },
    } 
    #using code from https://github.com/GregTJ/stable-fluids
    def createfluidmask(self, frames, width, height, invert, inflow_count, inflow_velocity, inflow_radius, inflow_padding, inflow_duration):
        from ..utility.fluid import Fluid
        from scipy.spatial import erf
        out = []
        masks = []
        RESOLUTION = width, height
        DURATION = frames

        INFLOW_PADDING = inflow_padding
        INFLOW_DURATION = inflow_duration
        INFLOW_RADIUS = inflow_radius
        INFLOW_VELOCITY = inflow_velocity
        INFLOW_COUNT = inflow_count

        print('Generating fluid solver, this may take some time.')
        fluid = Fluid(RESOLUTION, 'dye')

        center = np.floor_divide(RESOLUTION, 2)
        r = np.min(center) - INFLOW_PADDING

        points = np.linspace(-np.pi, np.pi, INFLOW_COUNT, endpoint=False)
        points = tuple(np.array((np.cos(p), np.sin(p))) for p in points)
        normals = tuple(-p for p in points)
        points = tuple(r * p + center for p in points)

        inflow_velocity = np.zeros_like(fluid.velocity)
        inflow_dye = np.zeros(fluid.shape)
        for p, n in zip(points, normals):
            mask = np.linalg.norm(fluid.indices - p[:, None, None], axis=0) <= INFLOW_RADIUS
            inflow_velocity[:, mask] += n[:, None] * INFLOW_VELOCITY
            inflow_dye[mask] = 1

        
        for f in range(DURATION):
            print(f'Computing frame {f + 1} of {DURATION}.')
            if f <= INFLOW_DURATION:
                fluid.velocity += inflow_velocity
                fluid.dye += inflow_dye

            curl = fluid.step()[1]
            # Using the error function to make the contrast a bit higher. 
            # Any other sigmoid function e.g. smoothstep would work.
            curl = (erf(curl * 2) + 1) / 4

            color = np.dstack((curl, np.ones(fluid.shape), fluid.dye))
            color = (np.clip(color, 0, 1) * 255).astype('uint8')
            image = np.array(color).astype(np.float32) / 255.0
            image = torch.from_numpy(image)[None,]
            mask = image[:, :, :, 0] 
            masks.append(mask)
            out.append(image)
        
        if invert:
            return (1.0 - torch.cat(out, dim=0),1.0 - torch.cat(masks, dim=0),)
        return (torch.cat(out, dim=0),torch.cat(masks, dim=0),)

class CreateAudioMask:
    def __init__(self):
        try:
            import librosa
            self.librosa = librosa
        except ImportError:
            print("Can not import librosa. Install it with 'pip install librosa'")
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "createaudiomask"
    CATEGORY = "KJNodes/deprecated"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "invert": ("BOOLEAN", {"default": False}),
                 "frames": ("INT", {"default": 16,"min": 1, "max": 255, "step": 1}),
                 "scale": ("FLOAT", {"default": 0.5,"min": 0.0, "max": 2.0, "step": 0.01}),
                 "audio_path": ("STRING", {"default": "audio.wav"}),
                 "width": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
        },
    } 

    def createaudiomask(self, frames, width, height, invert, audio_path, scale):
             # Define the number of images in the batch
        batch_size = frames
        out = []
        masks = []
        if audio_path == "audio.wav": #I don't know why relative path won't work otherwise...
            audio_path = os.path.join(script_directory, audio_path)
        audio, sr = self.librosa.load(audio_path)
        spectrogram = np.abs(self.librosa.stft(audio))
        
        for i in range(batch_size):
           image = Image.new("RGB", (width, height), "black")
           draw = ImageDraw.Draw(image)
           frame = spectrogram[:, i]
           circle_radius = int(height * np.mean(frame))
           circle_radius *= scale
           circle_center = (width // 2, height // 2)  # Calculate the center of the image

           draw.ellipse([(circle_center[0] - circle_radius, circle_center[1] - circle_radius),
                      (circle_center[0] + circle_radius, circle_center[1] + circle_radius)],
                      fill='white')
             
           image = np.array(image).astype(np.float32) / 255.0
           image = torch.from_numpy(image)[None,]
           mask = image[:, :, :, 0] 
           masks.append(mask)
           out.append(image)

        if invert:
            return (1.0 - torch.cat(out, dim=0),)
        return (torch.cat(out, dim=0),torch.cat(masks, dim=0),)
       
class CreateGradientMask:
    
    RETURN_TYPES = ("MASK",)
    FUNCTION = "createmask"
    CATEGORY = "KJNodes/masking/generate"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "invert": ("BOOLEAN", {"default": False}),
                 "frames": ("INT", {"default": 0,"min": 0, "max": 255, "step": 1}),
                 "width": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
        },
    } 
    def createmask(self, frames, width, height, invert):
        # Define the number of images in the batch
        batch_size = frames
        out = []
        # Create an empty array to store the image batch
        image_batch = np.zeros((batch_size, height, width), dtype=np.float32)
        # Generate the black to white gradient for each image
        for i in range(batch_size):
            gradient = np.linspace(1.0, 0.0, width, dtype=np.float32)
            time = i / frames  # Calculate the time variable
            offset_gradient = gradient - time  # Offset the gradient values based on time
            image_batch[i] = offset_gradient.reshape(1, -1)
        output = torch.from_numpy(image_batch)
        mask = output
        out.append(mask)
        if invert:
            return (1.0 - torch.cat(out, dim=0),)
        return (torch.cat(out, dim=0),)

class CreateFadeMask:
    
    RETURN_TYPES = ("MASK",)
    FUNCTION = "createfademask"
    CATEGORY = "KJNodes/deprecated"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "invert": ("BOOLEAN", {"default": False}),
                 "frames": ("INT", {"default": 2,"min": 2, "max": 255, "step": 1}),
                 "width": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 256,"min": 16, "max": 4096, "step": 1}),
                 "interpolation": (["linear", "ease_in", "ease_out", "ease_in_out"],),
                 "start_level": ("FLOAT", {"default": 1.0,"min": 0.0, "max": 1.0, "step": 0.01}),
                 "midpoint_level": ("FLOAT", {"default": 0.5,"min": 0.0, "max": 1.0, "step": 0.01}),
                 "end_level": ("FLOAT", {"default": 0.0,"min": 0.0, "max": 1.0, "step": 0.01}),
                 "midpoint_frame": ("INT", {"default": 0,"min": 0, "max": 4096, "step": 1}),
        },
    } 
    
    def createfademask(self, frames, width, height, invert, interpolation, start_level, midpoint_level, end_level, midpoint_frame):
        def ease_in(t):
            return t * t

        def ease_out(t):
            return 1 - (1 - t) * (1 - t)

        def ease_in_out(t):
            return 3 * t * t - 2 * t * t * t

        batch_size = frames
        out = []
        image_batch = np.zeros((batch_size, height, width), dtype=np.float32)

        if midpoint_frame == 0:
            midpoint_frame = batch_size // 2

        for i in range(batch_size):
            if i <= midpoint_frame:
                t = i / midpoint_frame
                if interpolation == "ease_in":
                    t = ease_in(t)
                elif interpolation == "ease_out":
                    t = ease_out(t)
                elif interpolation == "ease_in_out":
                    t = ease_in_out(t)
                color = start_level - t * (start_level - midpoint_level)
            else:
                t = (i - midpoint_frame) / (batch_size - midpoint_frame)
                if interpolation == "ease_in":
                    t = ease_in(t)
                elif interpolation == "ease_out":
                    t = ease_out(t)
                elif interpolation == "ease_in_out":
                    t = ease_in_out(t)
                color = midpoint_level - t * (midpoint_level - end_level)

            color = np.clip(color, 0, 255)
            image = np.full((height, width), color, dtype=np.float32)
            image_batch[i] = image

        output = torch.from_numpy(image_batch)
        mask = output
        out.append(mask)

        if invert:
            return (1.0 - torch.cat(out, dim=0),)
        return (torch.cat(out, dim=0),)

class CreateFadeMaskAdvanced:
    
    RETURN_TYPES = ("MASK",)
    FUNCTION = "createfademask"
    CATEGORY = "KJNodes/masking/generate"
    DESCRIPTION = """
Create a batch of masks interpolated between given frames and values. 
Uses same syntax as Fizz' BatchValueSchedule.
First value is the frame index (not that this starts from 0, not 1) 
and the second value inside the brackets is the float value of the mask in range 0.0 - 1.0  

For example the default values:  
0:(0.0)  
7:(1.0)  
15:(0.0)  
  
Would create a mask batch fo 16 frames, starting from black, 
interpolating with the chosen curve to fully white at the 8th frame, 
and interpolating from that to fully black at the 16th frame.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "points_string": ("STRING", {"default": "0:(0.0),\n7:(1.0),\n15:(0.0)\n", "multiline": True}),
                 "invert": ("BOOLEAN", {"default": False}),
                 "frames": ("INT", {"default": 16,"min": 2, "max": 255, "step": 1}),
                 "width": ("INT", {"default": 512,"min": 1, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 512,"min": 1, "max": 4096, "step": 1}),
                 "interpolation": (["linear", "ease_in", "ease_out", "ease_in_out"],),
        },
    } 
    
    def createfademask(self, frames, width, height, invert, points_string, interpolation):
        def ease_in(t):
            return t * t
        
        def ease_out(t):
            return 1 - (1 - t) * (1 - t)

        def ease_in_out(t):
            return 3 * t * t - 2 * t * t * t
        
        # Parse the input string into a list of tuples
        points = []
        points_string = points_string.rstrip(',\n')
        for point_str in points_string.split(','):
            frame_str, color_str = point_str.split(':')
            frame = int(frame_str.strip())
            color = float(color_str.strip()[1:-1])  # Remove parentheses around color
            points.append((frame, color))

        # Check if the last frame is already in the points
        if len(points) == 0 or points[-1][0] != frames - 1:
            # If not, add it with the color of the last specified frame
            points.append((frames - 1, points[-1][1] if points else 0))

        # Sort the points by frame number
        points.sort(key=lambda x: x[0])

        batch_size = frames
        out = []
        image_batch = np.zeros((batch_size, height, width), dtype=np.float32)

        # Index of the next point to interpolate towards
        next_point = 1

        for i in range(batch_size):
            while next_point < len(points) and i > points[next_point][0]:
                next_point += 1

            # Interpolate between the previous point and the next point
            prev_point = next_point - 1
            t = (i - points[prev_point][0]) / (points[next_point][0] - points[prev_point][0])
            if interpolation == "ease_in":
                t = ease_in(t)
            elif interpolation == "ease_out":
                t = ease_out(t)
            elif interpolation == "ease_in_out":
                t = ease_in_out(t)
            elif interpolation == "linear":
                pass  # No need to modify `t` for linear interpolation

            color = points[prev_point][1] - t * (points[prev_point][1] - points[next_point][1])
            color = np.clip(color, 0, 255)
            image = np.full((height, width), color, dtype=np.float32)
            image_batch[i] = image

        output = torch.from_numpy(image_batch)
        mask = output
        out.append(mask)

        if invert:
            return (1.0 - torch.cat(out, dim=0),)
        return (torch.cat(out, dim=0),)
    
class ScaleBatchPromptSchedule:
    
    RETURN_TYPES = ("STRING",)
    FUNCTION = "scaleschedule"
    CATEGORY = "KJNodes"
    DESCRIPTION = """
Scales a batch schedule from Fizz' nodes BatchPromptSchedule
to a different frame count.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "input_str": ("STRING", {"forceInput": True,"default": "0:(0.0),\n7:(1.0),\n15:(0.0)\n"}),
                 "old_frame_count": ("INT", {"forceInput": True,"default": 1,"min": 1, "max": 4096, "step": 1}),
                 "new_frame_count": ("INT", {"forceInput": True,"default": 1,"min": 1, "max": 4096, "step": 1}),
                
        },
    } 
    
    def scaleschedule(self, old_frame_count, input_str, new_frame_count):
        print("input_str:", input_str)
        pattern = r'"(\d+)"\s*:\s*"(.*?)"(?:,|\Z)'
        frame_strings = dict(re.findall(pattern, input_str))
        
        # Calculate the scaling factor
        scaling_factor = (new_frame_count - 1) / (old_frame_count - 1)
        
        # Initialize a dictionary to store the new frame numbers and strings
        new_frame_strings = {}
        
        # Iterate over the frame numbers and strings
        for old_frame, string in frame_strings.items():
            # Calculate the new frame number
            new_frame = int(round(int(old_frame) * scaling_factor))
            
            # Store the new frame number and corresponding string
            new_frame_strings[new_frame] = string
        
        # Format the output string
        output_str = ', '.join([f'"{k}":"{v}"' for k, v in sorted(new_frame_strings.items())])
        print(output_str)
        return (output_str,)

class CrossFadeImages:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "crossfadeimages"
    CATEGORY = "KJNodes/image"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "images_1": ("IMAGE",),
                 "images_2": ("IMAGE",),
                 "interpolation": (["linear", "ease_in", "ease_out", "ease_in_out", "bounce", "elastic", "glitchy", "exponential_ease_out"],),
                 "transition_start_index": ("INT", {"default": 1,"min": 0, "max": 4096, "step": 1}),
                 "transitioning_frames": ("INT", {"default": 1,"min": 0, "max": 4096, "step": 1}),
                 "start_level": ("FLOAT", {"default": 0.0,"min": 0.0, "max": 1.0, "step": 0.01}),
                 "end_level": ("FLOAT", {"default": 1.0,"min": 0.0, "max": 1.0, "step": 0.01}),
        },
    } 
    
    def crossfadeimages(self, images_1, images_2, transition_start_index, transitioning_frames, interpolation, start_level, end_level):

        def crossfade(images_1, images_2, alpha):
            crossfade = (1 - alpha) * images_1 + alpha * images_2
            return crossfade
        def ease_in(t):
            return t * t
        def ease_out(t):
            return 1 - (1 - t) * (1 - t)
        def ease_in_out(t):
            return 3 * t * t - 2 * t * t * t
        def bounce(t):
            if t < 0.5:
                return self.ease_out(t * 2) * 0.5
            else:
                return self.ease_in((t - 0.5) * 2) * 0.5 + 0.5
        def elastic(t):
            return math.sin(13 * math.pi / 2 * t) * math.pow(2, 10 * (t - 1))
        def glitchy(t):
            return t + 0.1 * math.sin(40 * t)
        def exponential_ease_out(t):
            return 1 - (1 - t) ** 4

        easing_functions = {
            "linear": lambda t: t,
            "ease_in": ease_in,
            "ease_out": ease_out,
            "ease_in_out": ease_in_out,
            "bounce": bounce,
            "elastic": elastic,
            "glitchy": glitchy,
            "exponential_ease_out": exponential_ease_out,
        }

        crossfade_images = []

        alphas = torch.linspace(start_level, end_level, transitioning_frames)
        for i in range(transitioning_frames):
            alpha = alphas[i]
            image1 = images_1[i + transition_start_index]
            image2 = images_2[i + transition_start_index]
            easing_function = easing_functions.get(interpolation)
            alpha = easing_function(alpha)  # Apply the easing function to the alpha value

            crossfade_image = crossfade(image1, image2, alpha)
            crossfade_images.append(crossfade_image)
            
        # Convert crossfade_images to tensor
        crossfade_images = torch.stack(crossfade_images, dim=0)
        # Get the last frame result of the interpolation
        last_frame = crossfade_images[-1]
        # Calculate the number of remaining frames from images_2
        remaining_frames = len(images_2) - (transition_start_index + transitioning_frames)
        # Crossfade the remaining frames with the last used alpha value
        for i in range(remaining_frames):
            alpha = alphas[-1]
            image1 = images_1[i + transition_start_index + transitioning_frames]
            image2 = images_2[i + transition_start_index + transitioning_frames]
            easing_function = easing_functions.get(interpolation)
            alpha = easing_function(alpha)  # Apply the easing function to the alpha value

            crossfade_image = crossfade(image1, image2, alpha)
            crossfade_images = torch.cat([crossfade_images, crossfade_image.unsqueeze(0)], dim=0)
        # Append the beginning of images_1
        beginning_images_1 = images_1[:transition_start_index]
        crossfade_images = torch.cat([beginning_images_1, crossfade_images], dim=0)
        return (crossfade_images, )

class GetImageRangeFromBatch:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "imagesfrombatch"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Creates a new batch using images from the input,  
batch, starting from start_index.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "images": ("IMAGE",),
                 "start_index": ("INT", {"default": 0,"min": -1, "max": 4096, "step": 1}),
                 "num_frames": ("INT", {"default": 1,"min": 1, "max": 4096, "step": 1}),
        },
    } 
    
    def imagesfrombatch(self, images, start_index, num_frames):
        if start_index == -1:
            start_index = len(images) - num_frames
        if start_index < 0 or start_index >= len(images):
            raise ValueError("GetImageRangeFromBatch: Start index is out of range")
        end_index = start_index + num_frames
        if end_index > len(images):
            raise ValueError("GetImageRangeFromBatch: End index is out of range")
        chosen_images = images[start_index:end_index]
        return (chosen_images, )
    
class GetImagesFromBatchIndexed:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "indexedimagesfrombatch"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Selects and returns the images at the specified indices as an image batch.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "images": ("IMAGE",),
                 "indexes": ("STRING", {"default": "0, 1, 2", "multiline": True}),
        },
    } 
    
    def indexedimagesfrombatch(self, images, indexes):
        
        # Parse the indexes string into a list of integers
        index_list = [int(index.strip()) for index in indexes.split(',')]
        
        # Convert list of indices to a PyTorch tensor
        indices_tensor = torch.tensor(index_list, dtype=torch.long)
        
        # Select the images at the specified indices
        chosen_images = images[indices_tensor]
        
        return (chosen_images,)

class InsertImagesToBatchIndexed:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "insertimagesfrombatch"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Inserts images at the specified indices into the original image batch.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "original_images": ("IMAGE",),
                "images_to_insert": ("IMAGE",),
                "indexes": ("STRING", {"default": "0, 1, 2", "multiline": True}),
            },
        }
    
    def insertimagesfrombatch(self, original_images, images_to_insert, indexes):
        
        # Parse the indexes string into a list of integers
        index_list = [int(index.strip()) for index in indexes.split(',')]
        
        # Convert list of indices to a PyTorch tensor
        indices_tensor = torch.tensor(index_list, dtype=torch.long)
        
        # Ensure the images_to_insert is a tensor
        if not isinstance(images_to_insert, torch.Tensor):
            images_to_insert = torch.tensor(images_to_insert)
        
        # Insert the images at the specified indices
        for index, image in zip(indices_tensor, images_to_insert):
            original_images[index] = image
        
        return (original_images,)
        
class GetLatentsFromBatchIndexed:
    
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "indexedlatentsfrombatch"
    CATEGORY = "KJNodes"
    DESCRIPTION = """
Selects and returns the latents at the specified indices as an latent batch.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "latents": ("LATENT",),
                 "indexes": ("STRING", {"default": "0, 1, 2", "multiline": True}),
        },
    } 
    
    def indexedlatentsfrombatch(self, latents, indexes):
        
        samples = latents.copy()
        latent_samples = samples["samples"] 

        # Parse the indexes string into a list of integers
        index_list = [int(index.strip()) for index in indexes.split(',')]
        
        # Convert list of indices to a PyTorch tensor
        indices_tensor = torch.tensor(index_list, dtype=torch.long)
        
        # Select the latents at the specified indices
        chosen_latents = latent_samples[indices_tensor]

        samples["samples"] = chosen_latents
        return (samples,)
    
class ReplaceImagesInBatch:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "replace"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Replaces the images in a batch, starting from the specified start index,  
with the replacement images.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "original_images": ("IMAGE",),
                 "replacement_images": ("IMAGE",),
                 "start_index": ("INT", {"default": 1,"min": 0, "max": 4096, "step": 1}),
        },
    } 
    
    def replace(self, original_images, replacement_images, start_index):
        images = None
        if start_index >= len(original_images):
            raise ValueError("GetImageRangeFromBatch: Start index is out of range")
        end_index = start_index + len(replacement_images)
        if end_index > len(original_images):
            raise ValueError("GetImageRangeFromBatch: End index is out of range")
         # Create a copy of the original_images tensor
        original_images_copy = original_images.clone()
        original_images_copy[start_index:end_index] = replacement_images
        images = original_images_copy
        return (images, )
    

class ReverseImageBatch:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "reverseimagebatch"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Reverses the order of the images in a batch.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "images": ("IMAGE",),
        },
    } 
    
    def reverseimagebatch(self, images):
        reversed_images = torch.flip(images, [0])
        return (reversed_images, )
    


class CreateTextMask:

    RETURN_TYPES = ("IMAGE", "MASK",)
    FUNCTION = "createtextmask"
    CATEGORY = "KJNodes/text"
    DESCRIPTION = """
Creates a text image and mask.  
Looks for fonts from this folder:  
ComfyUI/custom_nodes/ComfyUI-KJNodes/fonts
  
If start_rotation and/or end_rotation are different values,  
creates animation between them.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "invert": ("BOOLEAN", {"default": False}),
                 "frames": ("INT", {"default": 1,"min": 1, "max": 4096, "step": 1}),
                 "text_x": ("INT", {"default": 0,"min": 0, "max": 4096, "step": 1}),
                 "text_y": ("INT", {"default": 0,"min": 0, "max": 4096, "step": 1}),
                 "font_size": ("INT", {"default": 32,"min": 8, "max": 4096, "step": 1}),
                 "font_color": ("STRING", {"default": "white"}),
                 "text": ("STRING", {"default": "HELLO!", "multiline": True}),
                 "font": (folder_paths.get_filename_list("kjnodes_fonts"), ),
                 "width": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
                 "start_rotation": ("INT", {"default": 0,"min": 0, "max": 359, "step": 1}),
                 "end_rotation": ("INT", {"default": 0,"min": -359, "max": 359, "step": 1}),
        },
    } 

    def createtextmask(self, frames, width, height, invert, text_x, text_y, text, font_size, font_color, font, start_rotation, end_rotation):
    # Define the number of images in the batch
        batch_size = frames
        out = []
        masks = []
        rotation = start_rotation
        if start_rotation != end_rotation:
            rotation_increment = (end_rotation - start_rotation) / (batch_size - 1)

        font_path = folder_paths.get_full_path("kjnodes_fonts", font)
        # Generate the text
        for i in range(batch_size):
            image = Image.new("RGB", (width, height), "black")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype(font_path, font_size)
            
            # Split the text into words
            words = text.split()
            
            # Initialize variables for line creation
            lines = []
            current_line = []
            current_line_width = 0
            try: #new pillow  
                # Iterate through words to create lines
                for word in words:
                    word_width = font.getbbox(word)[2]
                    if current_line_width + word_width <= width - 2 * text_x:
                        current_line.append(word)
                        current_line_width += word_width + font.getbbox(" ")[2] # Add space width
                    else:
                        lines.append(" ".join(current_line))
                        current_line = [word]
                        current_line_width = word_width
            except: #old pillow             
                for word in words:
                    word_width = font.getsize(word)[0]
                    if current_line_width + word_width <= width - 2 * text_x:
                        current_line.append(word)
                        current_line_width += word_width + font.getsize(" ")[0] # Add space width
                    else:
                        lines.append(" ".join(current_line))
                        current_line = [word]
                        current_line_width = word_width
            
            # Add the last line if it's not empty
            if current_line:
                lines.append(" ".join(current_line))
            
            # Draw each line of text separately
            y_offset = text_y
            for line in lines:
                text_width = font.getlength(line)
                text_height = font_size
                text_center_x = text_x + text_width / 2
                text_center_y = y_offset + text_height / 2
                try:
                    draw.text((text_x, y_offset), line, font=font, fill=font_color, features=['-liga'])
                except:
                    draw.text((text_x, y_offset), line, font=font, fill=font_color)
                y_offset += text_height # Move to the next line
            
            if start_rotation != end_rotation:
                image = image.rotate(rotation, center=(text_center_x, text_center_y))
                rotation += rotation_increment
            
            image = np.array(image).astype(np.float32) / 255.0
            image = torch.from_numpy(image)[None,]
            mask = image[:, :, :, 0] 
            masks.append(mask)
            out.append(image)
            
        if invert:
            return (1.0 - torch.cat(out, dim=0), 1.0 - torch.cat(masks, dim=0),)
        return (torch.cat(out, dim=0),torch.cat(masks, dim=0),)
        
class GrowMaskWithBlur:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mask": ("MASK",),
                "expand": ("INT", {"default": 0, "min": -MAX_RESOLUTION, "max": MAX_RESOLUTION, "step": 1}),
                "incremental_expandrate": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100.0, "step": 0.1}),
                "tapered_corners": ("BOOLEAN", {"default": True}),
                "flip_input": ("BOOLEAN", {"default": False}),
                "blur_radius": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 100,
                    "step": 0.1
                }),
                "lerp_alpha": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "decay_factor": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            },
            "optional": {
                "fill_holes": ("BOOLEAN", {"default": False}),
            },
        }

    CATEGORY = "KJNodes/masking"
    RETURN_TYPES = ("MASK", "MASK",)
    RETURN_NAMES = ("mask", "mask_inverted",)
    FUNCTION = "expand_mask"
    DESCRIPTION = """
# GrowMaskWithBlur
- mask: Input mask or mask batch
- expand: Expand or contract mask or mask batch by a given amount
- incremental_expandrate: increase expand rate by a given amount per frame
- tapered_corners: use tapered corners
- flip_input: flip input mask
- blur_radius: value higher than 0 will blur the mask
- lerp_alpha: alpha value for interpolation between frames
- decay_factor: decay value for interpolation between frames
- fill_holes: fill holes in the mask (slow)"""
    
    def expand_mask(self, mask, expand, tapered_corners, flip_input, blur_radius, incremental_expandrate, lerp_alpha, decay_factor, fill_holes=False):
        alpha = lerp_alpha
        decay = decay_factor
        if flip_input:
            mask = 1.0 - mask
        c = 0 if tapered_corners else 1
        kernel = np.array([[c, 1, c],
                           [1, 1, 1],
                           [c, 1, c]])
        growmask = mask.reshape((-1, mask.shape[-2], mask.shape[-1])).cpu()
        out = []
        previous_output = None
        current_expand = expand
        for m in growmask:
            output = m.numpy()
            for _ in range(abs(round(current_expand))):
                if current_expand < 0:
                    output = scipy.ndimage.grey_erosion(output, footprint=kernel)
                else:
                    output = scipy.ndimage.grey_dilation(output, footprint=kernel)
            if current_expand < 0:
                current_expand -= abs(incremental_expandrate)
            else:
                current_expand += abs(incremental_expandrate)
            if fill_holes:
                binary_mask = output > 0
                output = scipy.ndimage.binary_fill_holes(binary_mask)
                output = output.astype(np.float32) * 255
            output = torch.from_numpy(output)
            if alpha < 1.0 and previous_output is not None:
                # Interpolate between the previous and current frame
                output = alpha * output + (1 - alpha) * previous_output
            if decay < 1.0 and previous_output is not None:
                # Add the decayed previous output to the current frame
                output += decay * previous_output
                output = output / output.max()
            previous_output = output
            out.append(output)

        if blur_radius != 0:
            # Convert the tensor list to PIL images, apply blur, and convert back
            for idx, tensor in enumerate(out):
                # Convert tensor to PIL image
                pil_image = tensor2pil(tensor.cpu().detach())[0]
                # Apply Gaussian blur
                pil_image = pil_image.filter(ImageFilter.GaussianBlur(blur_radius))
                # Convert back to tensor
                out[idx] = pil2tensor(pil_image)
            blurred = torch.cat(out, dim=0)
            return (blurred, 1.0 - blurred)
        else:
            return (torch.stack(out, dim=0), 1.0 - torch.stack(out, dim=0),)

class ColorToMask:
    
    RETURN_TYPES = ("MASK",)
    FUNCTION = "clip"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Converts chosen RGB value to a mask.  
With batch inputs, the **per_batch**  
controls the number of images processed at once.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "images": ("IMAGE",),
                 "invert": ("BOOLEAN", {"default": False}),
                 "red": ("INT", {"default": 0,"min": 0, "max": 255, "step": 1}),
                 "green": ("INT", {"default": 0,"min": 0, "max": 255, "step": 1}),
                 "blue": ("INT", {"default": 0,"min": 0, "max": 255, "step": 1}),
                 "threshold": ("INT", {"default": 10,"min": 0, "max": 255, "step": 1}),
                 "per_batch": ("INT", {"default": 16, "min": 1, "max": 4096, "step": 1}),
        },
    } 

    def clip(self, images, red, green, blue, threshold, invert, per_batch):

        color = torch.tensor([red, green, blue], dtype=torch.uint8)  
        black = torch.tensor([0, 0, 0], dtype=torch.uint8)
        white = torch.tensor([255, 255, 255], dtype=torch.uint8)
        
        if invert:
            black, white = white, black

        steps = images.shape[0]
        pbar = comfy.utils.ProgressBar(steps)
        tensors_out = []
        
        for start_idx in range(0, images.shape[0], per_batch):

            # Calculate color distances
            color_distances = torch.norm(images[start_idx:start_idx+per_batch] * 255 - color, dim=-1)
            
            # Create a mask based on the threshold
            mask = color_distances <= threshold
            
            # Apply the mask to create new images
            mask_out = torch.where(mask.unsqueeze(-1), white, black).float()
            mask_out = mask_out.mean(dim=-1)

            tensors_out.append(mask_out.cpu())
            batch_count = mask_out.shape[0]
            pbar.update(batch_count)
       
        tensors_out = torch.cat(tensors_out, dim=0)
        tensors_out = torch.clamp(tensors_out, min=0.0, max=1.0)
        return tensors_out,
      
class ConditioningMultiCombine:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "inputcount": ("INT", {"default": 2, "min": 2, "max": 20, "step": 1}),
                "conditioning_1": ("CONDITIONING", ),
                "conditioning_2": ("CONDITIONING", ),
            },
    }

    RETURN_TYPES = ("CONDITIONING", "INT")
    RETURN_NAMES = ("combined", "inputcount")
    FUNCTION = "combine"
    CATEGORY = "KJNodes/masking/conditioning"
    DESCRIPTION = """
Combines multiple conditioning nodes into one
"""

    def combine(self, inputcount, **kwargs):
        from nodes import ConditioningCombine
        cond_combine_node = ConditioningCombine()
        cond = kwargs["conditioning_1"]
        for c in range(1, inputcount):
            new_cond = kwargs[f"conditioning_{c + 1}"]
            cond = cond_combine_node.combine(new_cond, cond)[0]
        return (cond, inputcount,)

class ImageBatchMulti:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "inputcount": ("INT", {"default": 2, "min": 2, "max": 1000, "step": 1}),
                "image_1": ("IMAGE", ),
                "image_2": ("IMAGE", ),
            },
    }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "combine"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Creates an image batch from multiple images.  
You can set how many inputs the node has,  
with the **inputcount** and clicking update.
"""

    def combine(self, inputcount, **kwargs):
        from nodes import ImageBatch
        image_batch_node = ImageBatch()
        image = kwargs["image_1"]
        for c in range(1, inputcount):
            new_image = kwargs[f"image_{c + 1}"]
            image, = image_batch_node.batch(new_image, image)
        return (image,)

class MaskBatchMulti:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "inputcount": ("INT", {"default": 2, "min": 2, "max": 1000, "step": 1}),
                "mask_1": ("MASK", ),
                "mask_2": ("MASK", ),
            },
    }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "combine"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Creates an image batch from multiple masks.  
You can set how many inputs the node has,  
with the **inputcount** and clicking update.
"""

    def combine(self, inputcount, **kwargs):
        mask = kwargs["mask_1"]
        for c in range(1, inputcount):
            new_mask = kwargs[f"mask_{c + 1}"]
            if mask.shape[1:] != new_mask.shape[1:]:
                new_mask = F.interpolate(new_mask.unsqueeze(1), size=(mask.shape[1], mask.shape[2]), mode="bicubic").squeeze(1)
            mask = torch.cat((mask, new_mask), dim=0)
        return (mask,)

class CondPassThrough:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "positive": ("CONDITIONING", ),
                "negative": ("CONDITIONING", ),
            }, 
    }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING",)
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "passthrough"
    CATEGORY = "KJNodes/misc"
    DESCRIPTION = """
    Simply passes through the positive and negative conditioning,
    workaround for Set node not allowing bypassed inputs.
"""

    def passthrough(self, positive, negative):
        return (positive, negative,)

def append_helper(t, mask, c, set_area_to_bounds, strength):
        n = [t[0], t[1].copy()]
        _, h, w = mask.shape
        n[1]['mask'] = mask
        n[1]['set_area_to_bounds'] = set_area_to_bounds
        n[1]['mask_strength'] = strength
        c.append(n)  

class ConditioningSetMaskAndCombine:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive_1": ("CONDITIONING", ),
                "negative_1": ("CONDITIONING", ),
                "positive_2": ("CONDITIONING", ),
                "negative_2": ("CONDITIONING", ),
                "mask_1": ("MASK", ),
                "mask_2": ("MASK", ),
                "mask_1_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_2_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "set_cond_area": (["default", "mask bounds"],),
            }
        }

    RETURN_TYPES = ("CONDITIONING","CONDITIONING",)
    RETURN_NAMES = ("combined_positive", "combined_negative",)
    FUNCTION = "append"
    CATEGORY = "KJNodes/masking/conditioning"
    DESCRIPTION = """
Bundles multiple conditioning mask and combine nodes into one,functionality is identical to ComfyUI native nodes
"""

    def append(self, positive_1, negative_1, positive_2, negative_2, mask_1, mask_2, set_cond_area, mask_1_strength, mask_2_strength):
        c = []
        c2 = []
        set_area_to_bounds = False
        if set_cond_area != "default":
            set_area_to_bounds = True
        if len(mask_1.shape) < 3:
            mask_1 = mask_1.unsqueeze(0)
        if len(mask_2.shape) < 3:
            mask_2 = mask_2.unsqueeze(0)
        for t in positive_1:
            append_helper(t, mask_1, c, set_area_to_bounds, mask_1_strength)
        for t in positive_2:
            append_helper(t, mask_2, c, set_area_to_bounds, mask_2_strength)
        for t in negative_1:
            append_helper(t, mask_1, c2, set_area_to_bounds, mask_1_strength)
        for t in negative_2:
            append_helper(t, mask_2, c2, set_area_to_bounds, mask_2_strength)
        return (c, c2)

class ConditioningSetMaskAndCombine3:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive_1": ("CONDITIONING", ),
                "negative_1": ("CONDITIONING", ),
                "positive_2": ("CONDITIONING", ),
                "negative_2": ("CONDITIONING", ),
                "positive_3": ("CONDITIONING", ),
                "negative_3": ("CONDITIONING", ),
                "mask_1": ("MASK", ),
                "mask_2": ("MASK", ),
                "mask_3": ("MASK", ),
                "mask_1_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_2_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_3_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "set_cond_area": (["default", "mask bounds"],),
            }
        }

    RETURN_TYPES = ("CONDITIONING","CONDITIONING",)
    RETURN_NAMES = ("combined_positive", "combined_negative",)
    FUNCTION = "append"
    CATEGORY = "KJNodes/masking/conditioning"
    DESCRIPTION = """
Bundles multiple conditioning mask and combine nodes into one,functionality is identical to ComfyUI native nodes
"""

    def append(self, positive_1, negative_1, positive_2, positive_3, negative_2, negative_3, mask_1, mask_2, mask_3, set_cond_area, mask_1_strength, mask_2_strength, mask_3_strength):
        c = []
        c2 = []
        set_area_to_bounds = False
        if set_cond_area != "default":
            set_area_to_bounds = True
        if len(mask_1.shape) < 3:
            mask_1 = mask_1.unsqueeze(0)
        if len(mask_2.shape) < 3:
            mask_2 = mask_2.unsqueeze(0)
        if len(mask_3.shape) < 3:
            mask_3 = mask_3.unsqueeze(0)
        for t in positive_1:
            append_helper(t, mask_1, c, set_area_to_bounds, mask_1_strength)
        for t in positive_2:
            append_helper(t, mask_2, c, set_area_to_bounds, mask_2_strength)
        for t in positive_3:
            append_helper(t, mask_3, c, set_area_to_bounds, mask_3_strength)
        for t in negative_1:
            append_helper(t, mask_1, c2, set_area_to_bounds, mask_1_strength)
        for t in negative_2:
            append_helper(t, mask_2, c2, set_area_to_bounds, mask_2_strength)
        for t in negative_3:
            append_helper(t, mask_3, c2, set_area_to_bounds, mask_3_strength)
        return (c, c2)

class ConditioningSetMaskAndCombine4:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive_1": ("CONDITIONING", ),
                "negative_1": ("CONDITIONING", ),
                "positive_2": ("CONDITIONING", ),
                "negative_2": ("CONDITIONING", ),
                "positive_3": ("CONDITIONING", ),
                "negative_3": ("CONDITIONING", ),
                "positive_4": ("CONDITIONING", ),
                "negative_4": ("CONDITIONING", ),
                "mask_1": ("MASK", ),
                "mask_2": ("MASK", ),
                "mask_3": ("MASK", ),
                "mask_4": ("MASK", ),
                "mask_1_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_2_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_3_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_4_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "set_cond_area": (["default", "mask bounds"],),
            }
        }

    RETURN_TYPES = ("CONDITIONING","CONDITIONING",)
    RETURN_NAMES = ("combined_positive", "combined_negative",)
    FUNCTION = "append"
    CATEGORY = "KJNodes/masking/conditioning"
    DESCRIPTION = """
Bundles multiple conditioning mask and combine nodes into one,functionality is identical to ComfyUI native nodes
"""

    def append(self, positive_1, negative_1, positive_2, positive_3, positive_4, negative_2, negative_3, negative_4, mask_1, mask_2, mask_3, mask_4, set_cond_area, mask_1_strength, mask_2_strength, mask_3_strength, mask_4_strength):
        c = []
        c2 = []
        set_area_to_bounds = False
        if set_cond_area != "default":
            set_area_to_bounds = True
        if len(mask_1.shape) < 3:
            mask_1 = mask_1.unsqueeze(0)
        if len(mask_2.shape) < 3:
            mask_2 = mask_2.unsqueeze(0)
        if len(mask_3.shape) < 3:
            mask_3 = mask_3.unsqueeze(0)
        if len(mask_4.shape) < 3:
            mask_4 = mask_4.unsqueeze(0)
        for t in positive_1:
            append_helper(t, mask_1, c, set_area_to_bounds, mask_1_strength)
        for t in positive_2:
            append_helper(t, mask_2, c, set_area_to_bounds, mask_2_strength)
        for t in positive_3:
            append_helper(t, mask_3, c, set_area_to_bounds, mask_3_strength)
        for t in positive_4:
            append_helper(t, mask_4, c, set_area_to_bounds, mask_4_strength)
        for t in negative_1:
            append_helper(t, mask_1, c2, set_area_to_bounds, mask_1_strength)
        for t in negative_2:
            append_helper(t, mask_2, c2, set_area_to_bounds, mask_2_strength)
        for t in negative_3:
            append_helper(t, mask_3, c2, set_area_to_bounds, mask_3_strength)
        for t in negative_4:
            append_helper(t, mask_4, c2, set_area_to_bounds, mask_4_strength)
        return (c, c2)

class ConditioningSetMaskAndCombine5:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive_1": ("CONDITIONING", ),
                "negative_1": ("CONDITIONING", ),
                "positive_2": ("CONDITIONING", ),
                "negative_2": ("CONDITIONING", ),
                "positive_3": ("CONDITIONING", ),
                "negative_3": ("CONDITIONING", ),
                "positive_4": ("CONDITIONING", ),
                "negative_4": ("CONDITIONING", ),
                "positive_5": ("CONDITIONING", ),
                "negative_5": ("CONDITIONING", ),
                "mask_1": ("MASK", ),
                "mask_2": ("MASK", ),
                "mask_3": ("MASK", ),
                "mask_4": ("MASK", ),
                "mask_5": ("MASK", ),
                "mask_1_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_2_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_3_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_4_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mask_5_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "set_cond_area": (["default", "mask bounds"],),
            }
        }

    RETURN_TYPES = ("CONDITIONING","CONDITIONING",)
    RETURN_NAMES = ("combined_positive", "combined_negative",)
    FUNCTION = "append"
    CATEGORY = "KJNodes/masking/conditioning"
    DESCRIPTION = """
Bundles multiple conditioning mask and combine nodes into one,functionality is identical to ComfyUI native nodes
"""

    def append(self, positive_1, negative_1, positive_2, positive_3, positive_4, positive_5, negative_2, negative_3, negative_4, negative_5, mask_1, mask_2, mask_3, mask_4, mask_5, set_cond_area, mask_1_strength, mask_2_strength, mask_3_strength, mask_4_strength, mask_5_strength):
        c = []
        c2 = []
        set_area_to_bounds = False
        if set_cond_area != "default":
            set_area_to_bounds = True
        if len(mask_1.shape) < 3:
            mask_1 = mask_1.unsqueeze(0)
        if len(mask_2.shape) < 3:
            mask_2 = mask_2.unsqueeze(0)
        if len(mask_3.shape) < 3:
            mask_3 = mask_3.unsqueeze(0)
        if len(mask_4.shape) < 3:
            mask_4 = mask_4.unsqueeze(0)
        if len(mask_5.shape) < 3:
            mask_5 = mask_5.unsqueeze(0)
        for t in positive_1:
            append_helper(t, mask_1, c, set_area_to_bounds, mask_1_strength)
        for t in positive_2:
            append_helper(t, mask_2, c, set_area_to_bounds, mask_2_strength)
        for t in positive_3:
            append_helper(t, mask_3, c, set_area_to_bounds, mask_3_strength)
        for t in positive_4:
            append_helper(t, mask_4, c, set_area_to_bounds, mask_4_strength)
        for t in positive_5:
            append_helper(t, mask_5, c, set_area_to_bounds, mask_5_strength)
        for t in negative_1:
            append_helper(t, mask_1, c2, set_area_to_bounds, mask_1_strength)
        for t in negative_2:
            append_helper(t, mask_2, c2, set_area_to_bounds, mask_2_strength)
        for t in negative_3:
            append_helper(t, mask_3, c2, set_area_to_bounds, mask_3_strength)
        for t in negative_4:
            append_helper(t, mask_4, c2, set_area_to_bounds, mask_4_strength)
        for t in negative_5:
            append_helper(t, mask_5, c2, set_area_to_bounds, mask_5_strength)
        return (c, c2)
    
class VRAM_Debug:
    
    @classmethod
    
    def INPUT_TYPES(s):
      return {
        "required": {
            
            "empty_cache": ("BOOLEAN", {"default": True}),
            "gc_collect": ("BOOLEAN", {"default": True}),
            "unload_all_models": ("BOOLEAN", {"default": False}),
        },
        "optional": {
            "any_input": (any, {}),
            "image_pass": ("IMAGE",),
            "model_pass": ("MODEL",),
        }
	}
        
    RETURN_TYPES = (any, "IMAGE","MODEL","INT", "INT",)
    RETURN_NAMES = ("any_output", "image_pass", "model_pass", "freemem_before", "freemem_after")
    FUNCTION = "VRAMdebug"
    CATEGORY = "KJNodes/misc"
    DESCRIPTION = """
Returns the inputs unchanged, they are only used as triggers,  
and performs comfy model management functions and garbage collection,  
reports free VRAM before and after the operations.
"""

    def VRAMdebug(self, gc_collect,empty_cache, unload_all_models, image_pass=None, model_pass=None, any_input=None):
        freemem_before = model_management.get_free_memory()
        print("VRAMdebug: free memory before: ", freemem_before)
        if empty_cache:
            model_management.soft_empty_cache()
        if unload_all_models:
            model_management.unload_all_models()
        if gc_collect:
            import gc
            gc.collect()
        freemem_after = model_management.get_free_memory()
        print("VRAMdebug: free memory after: ", freemem_after)
        print("VRAMdebug: freed memory: ", freemem_after - freemem_before)
        return (any_input, image_pass, model_pass, freemem_before, freemem_after)

class SomethingToString:
    @classmethod
    
    def INPUT_TYPES(s):
     return {
        "required": {
        "input": (any, {}),
    },
    "optional": {
        "prefix": ("STRING", {"default": ""}),
        "suffix": ("STRING", {"default": ""}),
    }
    }
    RETURN_TYPES = ("STRING",)
    FUNCTION = "stringify"
    CATEGORY = "KJNodes/text"
    DESCRIPTION = """
Converts any type to a string.
"""

    def stringify(self, input, prefix="", suffix=""):
        if isinstance(input, (int, float, bool)):   
            stringified = str(input)
            if prefix:  # Check if prefix is not empty
                stringified = prefix + stringified  # Add the prefix
            if suffix:  # Check if suffix is not empty
                stringified = stringified + suffix  # Add the suffix
        else:
            return
        return (stringified,)
    
class ImagePass:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
            },
        }
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "passthrough"
    CATEGORY = "KJNodes/misc"
    DESCRIPTION = """
Passes the image through without modifying it.
"""

    def passthrough(self, image):
        return image,

class Sleep:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "input": (any, {}),
                "minutes": ("INT", {"default": 0, "min": 0, "max": 1439}),
                "seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 59.99, "step": 0.01}),
            },
        }
    RETURN_TYPES = (any,)
    FUNCTION = "sleepdelay"
    CATEGORY = "KJNodes/misc"
    DESCRIPTION = """
Delays the execution for the input amount of time.
"""

    def sleepdelay(self, input, minutes, seconds):
        total_seconds = minutes * 60 + seconds
        time.sleep(total_seconds)
        return input,
    
class EmptyLatentImagePresets:
    @classmethod
    def INPUT_TYPES(cls):  
        return {
        "required": {
            "dimensions": (
            [   '512 x 512',
                '768 x 512',
                '960 x 512',
                '1024 x 512',
                '1536 x 640',
                '1344 x 768',
                '1216 x 832',
                '1152 x 896',
                '1024 x 1024',
            ],
            {
            "default": '512 x 512'
             }),
           
            "invert": ("BOOLEAN", {"default": False}),
            "batch_size": ("INT", {
            "default": 1,
            "min": 1,
            "max": 4096
            }),
        },
        }

    RETURN_TYPES = ("LATENT", "INT", "INT")
    RETURN_NAMES = ("Latent", "Width", "Height")
    FUNCTION = "generate"
    CATEGORY = "KJNodes"

    def generate(self, dimensions, invert, batch_size):
        from nodes import EmptyLatentImage
        result = [x.strip() for x in dimensions.split('x')]
        
        if invert:
            width = int(result[1].split(' ')[0])
            height = int(result[0])
        else:
            width = int(result[0])
            height = int(result[1].split(' ')[0])
        latent = EmptyLatentImage().generate(width, height, batch_size)[0]

        return (latent, int(width), int(height),)

class ColorMatch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_ref": ("IMAGE",),
                "image_target": ("IMAGE",),
                "method": (
            [   
                'mkl',
                'hm', 
                'reinhard', 
                'mvgd', 
                'hm-mvgd-hm', 
                'hm-mkl-hm',
            ], {
               "default": 'mkl'
            }),
                
            },
        }
    
    CATEGORY = "KJNodes/image"

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "colormatch"
    DESCRIPTION = """
color-matcher enables color transfer across images which comes in handy for automatic  
color-grading of photographs, paintings and film sequences as well as light-field  
and stopmotion corrections.  

The methods behind the mappings are based on the approach from Reinhard et al.,  
the Monge-Kantorovich Linearization (MKL) as proposed by Pitie et al. and our analytical solution  
to a Multi-Variate Gaussian Distribution (MVGD) transfer in conjunction with classical histogram   
matching. As shown below our HM-MVGD-HM compound outperforms existing methods.   
https://github.com/hahnec/color-matcher/

"""
    
    def colormatch(self, image_ref, image_target, method):
        try:
            from color_matcher import ColorMatcher
        except:
            raise Exception("Can't import color-matcher, did you install requirements.txt? Manual install: pip install color-matcher")
        cm = ColorMatcher()
        image_ref = image_ref.cpu()
        image_target = image_target.cpu()
        batch_size = image_target.size(0)
        out = []
        images_target = image_target.squeeze()
        images_ref = image_ref.squeeze()

        image_ref_np = images_ref.numpy()
        images_target_np = images_target.numpy()

        if image_ref.size(0) > 1 and image_ref.size(0) != batch_size:
            raise ValueError("ColorMatch: Use either single reference image or a matching batch of reference images.")

        for i in range(batch_size):
            image_target_np = images_target_np if batch_size == 1 else images_target[i].numpy()
            image_ref_np_i = image_ref_np if image_ref.size(0) == 1 else images_ref[i].numpy()
            try:
                image_result = cm.transfer(src=image_target_np, ref=image_ref_np_i, method=method)
            except BaseException as e:
                print(f"Error occurred during transfer: {e}")
                break
            out.append(torch.from_numpy(image_result))
        return (torch.stack(out, dim=0).to(torch.float32), )
    
class SaveImageWithAlpha:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""

    @classmethod
    def INPUT_TYPES(s):
        return {"required": 
                    {"images": ("IMAGE", ),
                    "mask": ("MASK", ),
                    "filename_prefix": ("STRING", {"default": "ComfyUI"})},
                "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
                }

    RETURN_TYPES = ()
    FUNCTION = "save_images_alpha"
    OUTPUT_NODE = True
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Saves an image and mask as .PNG with the mask as the alpha channel. 
"""

    def save_images_alpha(self, images, mask, filename_prefix="ComfyUI_image_with_alpha", prompt=None, extra_pnginfo=None):
        from comfy.cli_args import args
        from PIL.PngImagePlugin import PngInfo
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[0])
        results = list()
        if mask.dtype == torch.float16:
            mask = mask.to(torch.float32)
        def file_counter():
            max_counter = 0
            # Loop through the existing files
            for existing_file in os.listdir(full_output_folder):
                # Check if the file matches the expected format
                match = re.fullmatch(fr"{filename}_(\d+)_?\.[a-zA-Z0-9]+", existing_file)
                if match:
                    # Extract the numeric portion of the filename
                    file_counter = int(match.group(1))
                    # Update the maximum counter value if necessary
                    if file_counter > max_counter:
                        max_counter = file_counter
            return max_counter

        for image, alpha in zip(images, mask):
            i = 255. * image.cpu().numpy()
            a = 255. * alpha.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
            
             # Resize the mask to match the image size
            a_resized = Image.fromarray(a).resize(img.size, Image.LANCZOS)
            a_resized = np.clip(a_resized, 0, 255).astype(np.uint8)
            img.putalpha(Image.fromarray(a_resized, mode='L'))
            metadata = None
            if not args.disable_metadata:
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for x in extra_pnginfo:
                        metadata.add_text(x, json.dumps(extra_pnginfo[x]))
           
            # Increment the counter by 1 to get the next available value
            counter = file_counter() + 1
            file = f"{filename}_{counter:05}.png"
            img.save(os.path.join(full_output_folder, file), pnginfo=metadata, compress_level=4)
            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type
            })

        return { "ui": { "images": results } }

class ImageConcanate:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "image1": ("IMAGE",),
            "image2": ("IMAGE",),
            "direction": (
            [   'right',
                'down',
                'left',
                'up',
            ],
            {
            "default": 'right'
             }),
            "match_image_size": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "concanate"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Concatenates the image2 to image1 in the specified direction.
"""

    def concanate(self, image1, image2, direction, match_image_size):
        if match_image_size:
            image2 = torch.nn.functional.interpolate(image2, size=(image1.shape[2], image1.shape[3]), mode="bilinear")
        if direction == 'right':
            row = torch.cat((image1, image2), dim=2)
        elif direction == 'down':
            row = torch.cat((image1, image2), dim=1)
        elif direction == 'left':
            row = torch.cat((image2, image1), dim=2)
        elif direction == 'up':
            row = torch.cat((image2, image1), dim=1)
        return (row,)
    
class ImageGridComposite2x2:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "image1": ("IMAGE",),
            "image2": ("IMAGE",),
            "image3": ("IMAGE",),
            "image4": ("IMAGE",),   
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "compositegrid"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Concatenates the 4 input images into a 2x2 grid. 
"""

    def compositegrid(self, image1, image2, image3, image4):
        top_row = torch.cat((image1, image2), dim=2)
        bottom_row = torch.cat((image3, image4), dim=2)
        grid = torch.cat((top_row, bottom_row), dim=1)
        return (grid,)
    
class ImageGridComposite3x3:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "image1": ("IMAGE",),
            "image2": ("IMAGE",),
            "image3": ("IMAGE",),
            "image4": ("IMAGE",),
            "image5": ("IMAGE",),
            "image6": ("IMAGE",),
            "image7": ("IMAGE",),
            "image8": ("IMAGE",),
            "image9": ("IMAGE",),     
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "compositegrid"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Concatenates the 9 input images into a 3x3 grid. 
"""

    def compositegrid(self, image1, image2, image3, image4, image5, image6, image7, image8, image9):
        top_row = torch.cat((image1, image2, image3), dim=2)
        mid_row = torch.cat((image4, image5, image6), dim=2)
        bottom_row = torch.cat((image7, image8, image9), dim=2)
        grid = torch.cat((top_row, mid_row, bottom_row), dim=1)
        return (grid,)
    
class ImageBatchTestPattern:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "batch_size": ("INT", {"default": 1,"min": 1, "max": 255, "step": 1}),
            "start_from": ("INT", {"default": 0,"min": 0, "max": 255, "step": 1}),
            "text_x": ("INT", {"default": 256,"min": 0, "max": 4096, "step": 1}),
            "text_y": ("INT", {"default": 256,"min": 0, "max": 4096, "step": 1}),
            "width": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
            "height": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
            "font": (folder_paths.get_filename_list("kjnodes_fonts"), ),
            "font_size": ("INT", {"default": 255,"min": 8, "max": 4096, "step": 1}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "generatetestpattern"
    CATEGORY = "KJNodes/text"

    def generatetestpattern(self, batch_size, font, font_size, start_from, width, height, text_x, text_y):
        out = []
        # Generate the sequential numbers for each image
        numbers = np.arange(start_from, start_from + batch_size)
        font_path = folder_paths.get_full_path("kjnodes_fonts", font)

        for number in numbers:
            # Create a black image with the number as a random color text
            image = Image.new("RGB", (width, height), color='black')
            draw = ImageDraw.Draw(image)
            
            # Generate a random color for the text
            font_color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            
            font = ImageFont.truetype(font_path, font_size)
            
            # Get the size of the text and position it in the center
            text = str(number)
           
            try:
                draw.text((text_x, text_y), text, font=font, fill=font_color, features=['-liga'])
            except:
                draw.text((text_x, text_y), text, font=font, fill=font_color,)
            
            # Convert the image to a numpy array and normalize the pixel values
            image_np = np.array(image).astype(np.float32) / 255.0
            image_tensor = torch.from_numpy(image_np).unsqueeze(0)
            out.append(image_tensor)
        out_tensor = torch.cat(out, dim=0)
  
        return (out_tensor,)

class BatchCLIPSeg:

    def __init__(self):
        pass
    
    @classmethod
    def INPUT_TYPES(s):
       
        return {"required":
                    {
                        "images": ("IMAGE",),
                        "text": ("STRING", {"multiline": False}),
                        "threshold": ("FLOAT", {"default": 0.1,"min": 0.0, "max": 10.0, "step": 0.001}),
                        "binary_mask": ("BOOLEAN", {"default": True}),
                        "combine_mask": ("BOOLEAN", {"default": False}),
                        "use_cuda": ("BOOLEAN", {"default": True}),
                     },
                }

    CATEGORY = "KJNodes/masking"
    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("Mask",)
    FUNCTION = "segment_image"
    DESCRIPTION = """
Segments an image or batch of images using CLIPSeg.
"""

    def segment_image(self, images, text, threshold, binary_mask, combine_mask, use_cuda):        
        from transformers import CLIPSegProcessor, CLIPSegForImageSegmentation
        out = []
        height, width, _ = images[0].shape
        if use_cuda and torch.cuda.is_available():
            device = torch.device("cuda")
        else:
            device = torch.device("cpu")
        dtype = comfy.model_management.unet_dtype()
        model = CLIPSegForImageSegmentation.from_pretrained("CIDAS/clipseg-rd64-refined")
        model.to(dtype)
        model.to(device)
        images = images.to(device)
        processor = CLIPSegProcessor.from_pretrained("CIDAS/clipseg-rd64-refined")
        pbar = comfy.utils.ProgressBar(images.shape[0])
        autocast_condition = (dtype != torch.float32) and not comfy.model_management.is_device_mps(device)
        with torch.autocast(comfy.model_management.get_autocast_device(device), dtype=dtype) if autocast_condition else nullcontext():
            for image in images:
                image = (image* 255).type(torch.uint8)
                prompt = text
                input_prc = processor(text=prompt, images=image, return_tensors="pt")
                # Move the processed input to the device
                for key in input_prc:
                    input_prc[key] = input_prc[key].to(device)
                
                outputs = model(**input_prc)
    
                tensor = torch.sigmoid(outputs[0])
                tensor_thresholded = torch.where(tensor > threshold, tensor, torch.tensor(0, dtype=torch.float))
                tensor_normalized = (tensor_thresholded - tensor_thresholded.min()) / (tensor_thresholded.max() - tensor_thresholded.min())
                tensor = tensor_normalized

                # Resize the mask
                if len(tensor.shape) == 3:
                    tensor = tensor.unsqueeze(0)
                resized_tensor = F.interpolate(tensor, size=(height, width), mode='nearest')

                # Remove the extra dimensions
                resized_tensor = resized_tensor[0, 0, :, :]
                pbar.update(1)
                out.append(resized_tensor)
          
        results = torch.stack(out).cpu().float()
        
        if combine_mask:
            combined_results = torch.max(results, dim=0)[0]
            results = combined_results.unsqueeze(0).repeat(len(images),1,1)

        if binary_mask:
            results = results.round()
        
        return results,

class RoundMask:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "mask": ("MASK",),  
        }}

    RETURN_TYPES = ("MASK",)
    FUNCTION = "round"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Rounds the mask or batch of masks to a binary mask.  
<img src="https://github.com/kijai/ComfyUI-KJNodes/assets/40791699/52c85202-f74e-4b96-9dac-c8bda5ddcc40" width="300" height="250" alt="RoundMask example">

"""

    def round(self, mask):
        mask = mask.round()
        return (mask,)
    
class ResizeMask:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mask": ("MASK",),
                "width": ("INT", { "default": 512, "min": 0, "max": MAX_RESOLUTION, "step": 8, "display": "number" }),
                "height": ("INT", { "default": 512, "min": 0, "max": MAX_RESOLUTION, "step": 8, "display": "number" }),
                "keep_proportions": ("BOOLEAN", { "default": False }),
            }
        }

    RETURN_TYPES = ("MASK", "INT", "INT",)
    RETURN_NAMES = ("mask", "width", "height",)
    FUNCTION = "resize"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Resizes the mask or batch of masks to the specified width and height.
"""

    def resize(self, mask, width, height, keep_proportions):
        if keep_proportions:
            _, oh, ow, _ = mask.shape
            width = ow if width == 0 else width
            height = oh if height == 0 else height
            ratio = min(width / ow, height / oh)
            width = round(ow*ratio)
            height = round(oh*ratio)
    
        outputs = mask.unsqueeze(0)  # Add an extra dimension for batch size
        outputs = F.interpolate(outputs, size=(height, width), mode="nearest")
        outputs = outputs.squeeze(0)  # Remove the extra dimension after interpolation

        return(outputs, outputs.shape[2], outputs.shape[1],)
    
class OffsetMask:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mask": ("MASK",),
                "x": ("INT", { "default": 0, "min": -4096, "max": MAX_RESOLUTION, "step": 1, "display": "number" }),
                "y": ("INT", { "default": 0, "min": -4096, "max": MAX_RESOLUTION, "step": 1, "display": "number" }),
                "angle": ("INT", { "default": 0, "min": -360, "max": 360, "step": 1, "display": "number" }),
                "duplication_factor": ("INT", { "default": 1, "min": 1, "max": 1000, "step": 1, "display": "number" }),
                "roll": ("BOOLEAN", { "default": False }),
                "incremental": ("BOOLEAN", { "default": False }),
                "padding_mode": (
            [   
                'empty',
                'border',
                'reflection',
                
            ], {
               "default": 'empty'
            }),
            }
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "offset"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Offsets the mask by the specified amount.  
 - mask: Input mask or mask batch
 - x: Horizontal offset
 - y: Vertical offset
 - angle: Angle in degrees
 - roll: roll edge wrapping
 - duplication_factor: Number of times to duplicate the mask to form a batch
 - border padding_mode: Padding mode for the mask
"""

    def offset(self, mask, x, y, angle, roll=False, incremental=False, duplication_factor=1, padding_mode="empty"):
        # Create duplicates of the mask batch
        mask = mask.repeat(duplication_factor, 1, 1).clone()

        batch_size, height, width = mask.shape

        if angle != 0 and incremental:
            for i in range(batch_size):
                rotation_angle = angle * (i+1)
                mask[i] = TF.rotate(mask[i].unsqueeze(0), rotation_angle).squeeze(0)
        elif angle > 0:
            for i in range(batch_size):
                mask[i] = TF.rotate(mask[i].unsqueeze(0), angle).squeeze(0)

        if roll:
            if incremental:
                for i in range(batch_size):
                    shift_x = min(x*(i+1), width-1)
                    shift_y = min(y*(i+1), height-1)
                    if shift_x != 0:
                        mask[i] = torch.roll(mask[i], shifts=shift_x, dims=1)
                    if shift_y != 0:
                        mask[i] = torch.roll(mask[i], shifts=shift_y, dims=0)
            else:
                shift_x = min(x, width-1)
                shift_y = min(y, height-1)
                if shift_x != 0:
                    mask = torch.roll(mask, shifts=shift_x, dims=2)
                if shift_y != 0:
                    mask = torch.roll(mask, shifts=shift_y, dims=1)
        else:
            
            for i in range(batch_size):
                if incremental:
                    temp_x = min(x * (i+1), width-1)
                    temp_y = min(y * (i+1), height-1)
                else:
                    temp_x = min(x, width-1)
                    temp_y = min(y, height-1)
                if temp_x > 0:
                    if padding_mode == 'empty':
                        mask[i] = torch.cat([torch.zeros((height, temp_x)), mask[i, :, :-temp_x]], dim=1)
                    elif padding_mode in ['replicate', 'reflect']:
                        mask[i] = F.pad(mask[i, :, :-temp_x], (0, temp_x), mode=padding_mode)
                elif temp_x < 0:
                    if padding_mode == 'empty':
                        mask[i] = torch.cat([mask[i, :, :temp_x], torch.zeros((height, -temp_x))], dim=1)
                    elif padding_mode in ['replicate', 'reflect']:
                        mask[i] = F.pad(mask[i, :, -temp_x:], (temp_x, 0), mode=padding_mode)

                if temp_y > 0:
                    if padding_mode == 'empty':
                        mask[i] = torch.cat([torch.zeros((temp_y, width)), mask[i, :-temp_y, :]], dim=0)
                    elif padding_mode in ['replicate', 'reflect']:
                        mask[i] = F.pad(mask[i, :-temp_y, :], (0, temp_y), mode=padding_mode)
                elif temp_y < 0:
                    if padding_mode == 'empty':
                        mask[i] = torch.cat([mask[i, :temp_y, :], torch.zeros((-temp_y, width))], dim=0)
                    elif padding_mode in ['replicate', 'reflect']:
                        mask[i] = F.pad(mask[i, -temp_y:, :], (temp_y, 0), mode=padding_mode)
           
        return mask,


class WidgetToString:
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "id": ("INT", {"default": 0}),
                "widget_name": ("STRING", {"multiline": False}),
                "return_all": ("BOOLEAN", {"default": False}),
            },
            
            "hidden": {"extra_pnginfo": "EXTRA_PNGINFO",
                       "prompt": "PROMPT"},
        }

    RETURN_TYPES = ("STRING", )
    FUNCTION = "get_widget_value"
    CATEGORY = "KJNodes/text"
    DESCRIPTION = """
Selects a node and it's specified widget and outputs the value as a string.  
To see node id's, enable node id display from Manager badge menu.
"""

    def get_widget_value(self, id, widget_name, extra_pnginfo, prompt, return_all=False):
        workflow = extra_pnginfo["workflow"]
        print(workflow)
        results = []
        for node in workflow["nodes"]:
            print(node)
            node_id = node["id"]

            if node_id != id:
                continue

            values = prompt[str(node_id)]
            if "inputs" in values:
                if return_all:
                    results.append(', '.join(f'{k}: {str(v)}' for k, v in values["inputs"].items()))
                elif widget_name in values["inputs"]:
                    v = str(values["inputs"][widget_name])  # Convert to string here
                    return (v, )
                else:
                    raise NameError(f"Widget not found: {id}.{widget_name}")
        if not results:
            raise NameError(f"Node not found: {id}")
        return (', '.join(results).strip(', '), )

class CreateShapeMask:
    
    RETURN_TYPES = ("MASK", "MASK",)
    RETURN_NAMES = ("mask", "mask_inverted",)
    FUNCTION = "createshapemask"
    CATEGORY = "KJNodes/masking/generate"
    DESCRIPTION = """
Creates a mask or batch of masks with the specified shape.  
Locations are center locations.  
Grow value is the amount to grow the shape on each frame, creating animated masks.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "shape": (
            [   'circle',
                'square',
                'triangle',
            ],
            {
            "default": 'circle'
             }),
                "frames": ("INT", {"default": 1,"min": 1, "max": 4096, "step": 1}),
                "location_x": ("INT", {"default": 256,"min": 0, "max": 4096, "step": 1}),
                "location_y": ("INT", {"default": 256,"min": 0, "max": 4096, "step": 1}),
                "grow": ("INT", {"default": 0, "min": -512, "max": 512, "step": 1}),
                "frame_width": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
                "frame_height": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
                "shape_width": ("INT", {"default": 128,"min": 8, "max": 4096, "step": 1}),
                "shape_height": ("INT", {"default": 128,"min": 8, "max": 4096, "step": 1}),
        },
    } 

    def createshapemask(self, frames, frame_width, frame_height, location_x, location_y, shape_width, shape_height, grow, shape):
        # Define the number of images in the batch
        batch_size = frames
        out = []
        color = "white"
        for i in range(batch_size):
            image = Image.new("RGB", (frame_width, frame_height), "black")
            draw = ImageDraw.Draw(image)

            # Calculate the size for this frame and ensure it's not less than 0
            current_width = max(0, shape_width + i*grow)
            current_height = max(0, shape_height + i*grow)

            if shape == 'circle' or shape == 'square':
                # Define the bounding box for the shape
                left_up_point = (location_x - current_width // 2, location_y - current_height // 2)
                right_down_point = (location_x + current_width // 2, location_y + current_height // 2)
                two_points = [left_up_point, right_down_point]

                if shape == 'circle':
                    draw.ellipse(two_points, fill=color)
                elif shape == 'square':
                    draw.rectangle(two_points, fill=color)
                    
            elif shape == 'triangle':
                # Define the points for the triangle
                left_up_point = (location_x - current_width // 2, location_y + current_height // 2) # bottom left
                right_down_point = (location_x + current_width // 2, location_y + current_height // 2) # bottom right
                top_point = (location_x, location_y - current_height // 2) # top point
                draw.polygon([top_point, left_up_point, right_down_point], fill=color)

            image = pil2tensor(image)
            mask = image[:, :, :, 0]
            out.append(mask)
        outstack = torch.cat(out, dim=0)
        return (outstack, 1.0 - outstack,)
    
class CreateVoronoiMask:
    
    RETURN_TYPES = ("MASK", "MASK",)
    RETURN_NAMES = ("mask", "mask_inverted",)
    FUNCTION = "createvoronoi"
    CATEGORY = "KJNodes/masking/generate"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "frames": ("INT", {"default": 16,"min": 2, "max": 4096, "step": 1}),
                 "num_points": ("INT", {"default": 15,"min": 1, "max": 4096, "step": 1}),
                 "line_width": ("INT", {"default": 4,"min": 1, "max": 4096, "step": 1}),
                 "speed": ("FLOAT", {"default": 0.5,"min": 0.0, "max": 1.0, "step": 0.01}),
                 "frame_width": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
                 "frame_height": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
        },
    } 

    def createvoronoi(self, frames, num_points, line_width, speed, frame_width, frame_height):
        from scipy.spatial import Voronoi
        # Define the number of images in the batch
        batch_size = frames
        out = []
          
        # Calculate aspect ratio
        aspect_ratio = frame_width / frame_height
        
        # Create start and end points for each point, considering the aspect ratio
        start_points = np.random.rand(num_points, 2)
        start_points[:, 0] *= aspect_ratio
        
        end_points = np.random.rand(num_points, 2)
        end_points[:, 0] *= aspect_ratio

        for i in range(batch_size):
            # Interpolate the points' positions based on the current frame
            t = (i * speed) / (batch_size - 1)  # normalize to [0, 1] over the frames
            t = np.clip(t, 0, 1)  # ensure t is in [0, 1]
            points = (1 - t) * start_points + t * end_points  # lerp

            # Adjust points for aspect ratio
            points[:, 0] *= aspect_ratio

            vor = Voronoi(points)

            # Create a blank image with a white background
            fig, ax = plt.subplots()
            plt.subplots_adjust(left=0, right=1, bottom=0, top=1)
            ax.set_xlim([0, aspect_ratio]); ax.set_ylim([0, 1])  # adjust x limits
            ax.axis('off')
            ax.margins(0, 0)
            fig.set_size_inches(aspect_ratio * frame_height/100, frame_height/100)  # adjust figure size
            ax.fill_between([0, 1], [0, 1], color='white')

            # Plot each Voronoi ridge
            for simplex in vor.ridge_vertices:
                simplex = np.asarray(simplex)
                if np.all(simplex >= 0):
                    plt.plot(vor.vertices[simplex, 0], vor.vertices[simplex, 1], 'k-', linewidth=line_width)

            fig.canvas.draw()
            img = np.array(fig.canvas.renderer._renderer)

            plt.close(fig)

            pil_img = Image.fromarray(img).convert("L")
            mask = torch.tensor(np.array(pil_img)) / 255.0

            out.append(mask)

        return (torch.stack(out, dim=0), 1.0 - torch.stack(out, dim=0),)

class CreateMagicMask:
    
    RETURN_TYPES = ("MASK", "MASK",)
    RETURN_NAMES = ("mask", "mask_inverted",)
    FUNCTION = "createmagicmask"
    CATEGORY = "KJNodes/masking/generate"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "frames": ("INT", {"default": 16,"min": 2, "max": 4096, "step": 1}),
                 "depth": ("INT", {"default": 12,"min": 1, "max": 500, "step": 1}),
                 "distortion": ("FLOAT", {"default": 1.5,"min": 0.0, "max": 100.0, "step": 0.01}),
                 "seed": ("INT", {"default": 123,"min": 0, "max": 99999999, "step": 1}),
                 "transitions": ("INT", {"default": 1,"min": 1, "max": 20, "step": 1}),
                 "frame_width": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
                 "frame_height": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
        },
    } 

    def createmagicmask(self, frames, transitions, depth, distortion, seed, frame_width, frame_height):
        from ..utility.magictex import coordinate_grid, random_transform, magic
        rng = np.random.default_rng(seed)
        out = []
        coords = coordinate_grid((frame_width, frame_height))

        # Calculate the number of frames for each transition
        frames_per_transition = frames // transitions

        # Generate a base set of parameters
        base_params = {
            "coords": random_transform(coords, rng),
            "depth": depth,
            "distortion": distortion,
        }
        for t in range(transitions):
        # Generate a second set of parameters that is at most max_diff away from the base parameters
            params1 = base_params.copy()
            params2 = base_params.copy()

            params1['coords'] = random_transform(coords, rng)
            params2['coords'] = random_transform(coords, rng)

            for i in range(frames_per_transition):
                # Compute the interpolation factor
                alpha = i / frames_per_transition

                # Interpolate between the two sets of parameters
                params = params1.copy()
                params['coords'] = (1 - alpha) * params1['coords'] + alpha * params2['coords']

                tex = magic(**params)

                dpi = frame_width / 10
                fig = plt.figure(figsize=(10, 10), dpi=dpi)

                ax = fig.add_subplot(111)
                plt.subplots_adjust(left=0, right=1, bottom=0, top=1)
                
                ax.get_yaxis().set_ticks([])
                ax.get_xaxis().set_ticks([])
                ax.imshow(tex, aspect='auto')
                
                fig.canvas.draw()
                img = np.array(fig.canvas.renderer._renderer)
                
                plt.close(fig)
                
                pil_img = Image.fromarray(img).convert("L")
                mask = torch.tensor(np.array(pil_img)) / 255.0
                
                out.append(mask)
        
        return (torch.stack(out, dim=0), 1.0 - torch.stack(out, dim=0),)

class BboxToInt:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bboxes": ("BBOX",),
                "index": ("INT", {"default": 0,"min": 0, "max": 99999999, "step": 1}),
            },
        }

    RETURN_TYPES = ("INT","INT","INT","INT","INT","INT",)
    RETURN_NAMES = ("x_min","y_min","width","height", "center_x","center_y",)
    FUNCTION = "bboxtoint"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Returns selected index from bounding box list as integers.
"""
    def bboxtoint(self, bboxes, index):
        x_min, y_min, width, height = bboxes[index]
        center_x = int(x_min + width / 2)
        center_y = int(y_min + height / 2)
        
        return (x_min, y_min, width, height, center_x, center_y,)

class BboxVisualize:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "bboxes": ("BBOX",),
                "line_width": ("INT", {"default": 1,"min": 1, "max": 10, "step": 1}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "visualizebbox"
    DESCRIPTION = """
Visualizes the specified bbox on the image.
"""

    CATEGORY = "KJNodes/masking"

    def visualizebbox(self, bboxes, images, line_width):
        image_list = []
        for image, bbox in zip(images, bboxes):
            x_min, y_min, width, height = bbox
            image = image.permute(2, 0, 1)

            img_with_bbox = image.clone()
            
            # Define the color for the bbox, e.g., red
            color = torch.tensor([1, 0, 0], dtype=torch.float32)
            
            # Draw lines for each side of the bbox with the specified line width
            for lw in range(line_width):
                # Top horizontal line
                img_with_bbox[:, y_min + lw, x_min:x_min + width] = color[:, None]
                
                # Bottom horizontal line
                img_with_bbox[:, y_min + height - lw, x_min:x_min + width] = color[:, None]
                
                # Left vertical line
                img_with_bbox[:, y_min:y_min + height, x_min + lw] = color[:, None]
                
                # Right vertical line
                img_with_bbox[:, y_min:y_min + height, x_min + width - lw] = color[:, None]
        
            img_with_bbox = img_with_bbox.permute(1, 2, 0).unsqueeze(0)
            image_list.append(img_with_bbox)

        return (torch.cat(image_list, dim=0),)

from PIL import ImageGrab
import time
class ImageGrabPIL:

    @classmethod
    def IS_CHANGED(cls):

        return

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "screencap"
    CATEGORY = "KJNodes/experimental"
    DESCRIPTION = """
Captures an area specified by screen coordinates.  
Can be used for realtime diffusion with autoqueue.
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "x": ("INT", {"default": 0,"min": 0, "max": 4096, "step": 1}),
                 "y": ("INT", {"default": 0,"min": 0, "max": 4096, "step": 1}),
                 "width": ("INT", {"default": 512,"min": 0, "max": 4096, "step": 1}),
                 "height": ("INT", {"default": 512,"min": 0, "max": 4096, "step": 1}),
                 "num_frames": ("INT", {"default": 1,"min": 1, "max": 255, "step": 1}),
                 "delay": ("FLOAT", {"default": 0.1,"min": 0.0, "max": 10.0, "step": 0.01}),
        },
    } 

    def screencap(self, x, y, width, height, num_frames, delay):
        captures = []
        bbox = (x, y, x + width, y + height)
        
        for _ in range(num_frames):
            # Capture screen
            screen_capture = ImageGrab.grab(bbox=bbox)
            screen_capture_torch = torch.tensor(np.array(screen_capture), dtype=torch.float32) / 255.0
            screen_capture_torch = screen_capture_torch.unsqueeze(0)
            captures.append(screen_capture_torch)
            
            # Wait for a short delay if more than one frame is to be captured
            if num_frames > 1:
                time.sleep(delay)
        
        return (torch.cat(captures, dim=0),)

class DummyLatentOut:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
            "latent": ("LATENT",),
            }
        }

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "dummy"
    CATEGORY = "KJNodes/misc"
    OUTPUT_NODE = True
    DESCRIPTION = """
Does nothing, used to trigger generic workflow output.    
A way to get previews in the UI without saving anything to disk.
"""

    def dummy(self, latent):
        return (latent,)
    
class FlipSigmasAdjusted:
    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {"sigmas": ("SIGMAS", ),
                     "divide_by_last_sigma": ("BOOLEAN", {"default": False}),
                     "divide_by": ("FLOAT", {"default": 1,"min": 1, "max": 255, "step": 0.01}),
                     "offset_by": ("INT", {"default": 1,"min": -100, "max": 100, "step": 1}),
                     }
                }
    RETURN_TYPES = ("SIGMAS", "STRING",)
    RETURN_NAMES = ("SIGMAS", "sigmas_string",)
    CATEGORY = "KJNodes/noise"
    FUNCTION = "get_sigmas_adjusted"

    def get_sigmas_adjusted(self, sigmas, divide_by_last_sigma, divide_by, offset_by):
        
        sigmas = sigmas.flip(0)
        if sigmas[0] == 0:
            sigmas[0] = 0.0001
        adjusted_sigmas = sigmas.clone()
        #offset sigma
        for i in range(1, len(sigmas)):
            offset_index = i - offset_by
            if 0 <= offset_index < len(sigmas):
                adjusted_sigmas[i] = sigmas[offset_index]
            else:
                adjusted_sigmas[i] = 0.0001 
        if adjusted_sigmas[0] == 0:
            adjusted_sigmas[0] = 0.0001  
        if divide_by_last_sigma:
            adjusted_sigmas = adjusted_sigmas / adjusted_sigmas[-1]

        sigma_np_array = adjusted_sigmas.numpy()
        array_string = np.array2string(sigma_np_array, precision=2, separator=', ', threshold=np.inf)
        adjusted_sigmas = adjusted_sigmas / divide_by
        return (adjusted_sigmas, array_string,)
    
class CustomSigmas:
    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {
                     "sigmas_string" :("STRING", {"default": "14.615, 6.475, 3.861, 2.697, 1.886, 1.396, 0.963, 0.652, 0.399, 0.152, 0.029","multiline": True}),
                     "interpolate_to_steps": ("INT", {"default": 10,"min": 0, "max": 255, "step": 1}),
                     }
                }
    RETURN_TYPES = ("SIGMAS",)
    RETURN_NAMES = ("SIGMAS",)
    CATEGORY = "KJNodes/noise"
    FUNCTION = "customsigmas"
    DESCRIPTION = """
Creates a sigmas tensor from a string of comma separated values.  
Examples: 
   
Nvidia's optimized AYS 10 step schedule for SD 1.5:  
14.615, 6.475, 3.861, 2.697, 1.886, 1.396, 0.963, 0.652, 0.399, 0.152, 0.029  
SDXL:   
14.615, 6.315, 3.771, 2.181, 1.342, 0.862, 0.555, 0.380, 0.234, 0.113, 0.029  
SVD:  
700.00, 54.5, 15.886, 7.977, 4.248, 1.789, 0.981, 0.403, 0.173, 0.034, 0.002  
"""
    def customsigmas(self, sigmas_string, interpolate_to_steps):
        sigmas_list = sigmas_string.split(', ')
        sigmas_float_list = [float(sigma) for sigma in sigmas_list]
        sigmas_tensor = torch.tensor(sigmas_float_list)
        if len(sigmas_tensor) != interpolate_to_steps:
            sigmas_tensor = self.loglinear_interp(sigmas_tensor, interpolate_to_steps)
        return (sigmas_tensor,)
     
    def loglinear_interp(self, t_steps, num_steps):
        """
        Performs log-linear interpolation of a given array of decreasing numbers.
        """
        t_steps_np = t_steps.numpy()

        xs = np.linspace(0, 1, len(t_steps_np))
        ys = np.log(t_steps_np[::-1])
        
        new_xs = np.linspace(0, 1, num_steps)
        new_ys = np.interp(new_xs, xs, ys)
        
        interped_ys = np.exp(new_ys)[::-1].copy()
        interped_ys_tensor = torch.tensor(interped_ys)
        return interped_ys_tensor

 
class InjectNoiseToLatent:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "latents":("LATENT",),  
            "strength": ("FLOAT", {"default": 0.1, "min": 0.0, "max": 200.0, "step": 0.0001}),
            "noise":  ("LATENT",),
            "normalize": ("BOOLEAN", {"default": False}),
            "average": ("BOOLEAN", {"default": False}),
            },
            "optional":{
                "mask": ("MASK", ),
                "mix_randn_amount": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1000.0, "step": 0.001}),
                "seed": ("INT", {"default": 123,"min": 0, "max": 0xffffffffffffffff, "step": 1}),
            }
            }
    
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "injectnoise"
    CATEGORY = "KJNodes/noise"
        
    def injectnoise(self, latents, strength, noise, normalize, average, mix_randn_amount=0, seed=None, mask=None):
        samples = latents.copy()
        if latents["samples"].shape != noise["samples"].shape:
            raise ValueError("InjectNoiseToLatent: Latent and noise must have the same shape")
        if average:
            noised = (samples["samples"].clone() + noise["samples"].clone()) / 2
        else:
            noised = samples["samples"].clone() + noise["samples"].clone() * strength
        if normalize:
            noised = noised / noised.std()
        if mask is not None:
            mask = torch.nn.functional.interpolate(mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])), size=(noised.shape[2], noised.shape[3]), mode="bilinear")
            mask = mask.expand((-1,noised.shape[1],-1,-1))
            if mask.shape[0] < noised.shape[0]:
                mask = mask.repeat((noised.shape[0] -1) // mask.shape[0] + 1, 1, 1, 1)[:noised.shape[0]]
            noised = mask * noised + (1-mask) * latents["samples"]
        if mix_randn_amount > 0:
            if seed is not None:
                torch.manual_seed(seed)
            rand_noise = torch.randn_like(noised)
            noised = ((1 - mix_randn_amount) * noised + mix_randn_amount *
                            rand_noise) / ((mix_randn_amount**2 + (1-mix_randn_amount)**2) ** 0.5)
        samples["samples"] = noised
        return (samples,)

class AddLabel:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "image":("IMAGE",),  
            "text_x": ("INT", {"default": 10, "min": 0, "max": 4096, "step": 1}),
            "text_y": ("INT", {"default": 2, "min": 0, "max": 4096, "step": 1}),
            "height": ("INT", {"default": 48, "min": 0, "max": 4096, "step": 1}),
            "font_size": ("INT", {"default": 32, "min": 0, "max": 4096, "step": 1}),
            "font_color": ("STRING", {"default": "white"}),
            "label_color": ("STRING", {"default": "black"}),
            "font": (folder_paths.get_filename_list("kjnodes_fonts"), ),
            "text": ("STRING", {"default": "Text"}),
            "direction": (
            [   'up',
                'down',
            ],
            {
            "default": 'up'
             }),
            },
            "optional":{
                "caption": ("STRING", {"default": "", "forceInput": True}),
            }
            }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "addlabel"
    CATEGORY = "KJNodes/text"
    DESCRIPTION = """
Creates a new with the given text, and concatenates it to  
either above or below the input image.  
Note that this changes the input image's height!  
Fonts are loaded from this folder:  
ComfyUI/custom_nodes/ComfyUI-KJNodes/fonts
"""
        
    def addlabel(self, image, text_x, text_y, text, height, font_size, font_color, label_color, font, direction, caption=""):
        batch_size = image.shape[0]
        width = image.shape[2]
        
        if font == "TTNorms-Black.otf":
            font_path = os.path.join(script_directory, "fonts", "TTNorms-Black.otf")
        else:
            font_path = folder_paths.get_full_path("kjnodes_fonts", font)
        
        if caption == "":
            label_image = Image.new("RGB", (width, height), label_color)
            draw = ImageDraw.Draw(label_image)
            font = ImageFont.truetype(font_path, font_size)
            try:
                draw.text((text_x, text_y), text, font=font, fill=font_color, features=['-liga'])
            except:
                draw.text((text_x, text_y), text, font=font, fill=font_color)

            label_image = np.array(label_image).astype(np.float32) / 255.0
            label_image = torch.from_numpy(label_image)[None, :, :, :]
            # Duplicate the label image for the entire batch
            label_batch = label_image.repeat(batch_size, 1, 1, 1)
        else:
            label_list = []
            assert len(caption) == batch_size, "Number of captions does not match number of images"
            for cap in caption:
                label_image = Image.new("RGB", (width, height), label_color)
                draw = ImageDraw.Draw(label_image)
                font = ImageFont.truetype(font_path, font_size)
                try:
                    draw.text((text_x, text_y), cap, font=font, fill=font_color, features=['-liga'])
                except:
                    draw.text((text_x, text_y), cap, font=font, fill=font_color)

                label_image = np.array(label_image).astype(np.float32) / 255.0
                label_image = torch.from_numpy(label_image)
                label_list.append(label_image)
            label_batch = torch.stack(label_list)
            print(label_batch.shape)

        if direction == 'down':
            combined_images = torch.cat((image, label_batch), dim=1)
        elif direction == 'up':
            combined_images = torch.cat((label_batch, image), dim=1)
        
        return (combined_images,)

 
class SoundReactive:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {  
            "sound_level": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 99999, "step": 0.01}),
            "start_range_hz": ("INT", {"default": 150, "min": 0, "max": 9999, "step": 1}),
            "end_range_hz": ("INT", {"default": 2000, "min": 0, "max": 9999, "step": 1}),
            "multiplier": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 99999, "step": 0.01}),
            "smoothing_factor": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
            "normalize": ("BOOLEAN", {"default": False}),
            },
            }
    
    RETURN_TYPES = ("FLOAT","INT",)
    RETURN_NAMES =("sound_level", "sound_level_int",)
    FUNCTION = "react"
    CATEGORY = "KJNodes/audio"
    DESCRIPTION = """
Reacts to the sound level of the input.  
Uses your browsers sound input options and requires.  
Meant to be used with realtime diffusion with autoqueue.
"""
        
    def react(self, sound_level, start_range_hz, end_range_hz, smoothing_factor, multiplier, normalize):

        sound_level *= multiplier

        if normalize:
            sound_level /= 255

        sound_level_int = int(sound_level)
        return (sound_level, sound_level_int, )     
       
class GenerateNoise:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { 
            "width": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
            "height": ("INT", {"default": 512,"min": 16, "max": 4096, "step": 1}),
            "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
            "seed": ("INT", {"default": 123,"min": 0, "max": 0xffffffffffffffff, "step": 1}),
            "multiplier": ("FLOAT", {"default": 1.0,"min": 0.0, "max": 4096, "step": 0.01}),
            "constant_batch_noise": ("BOOLEAN", {"default": False}),
            "normalize": ("BOOLEAN", {"default": False}),
            },
            "optional": {
            "model": ("MODEL", ),
            "sigmas": ("SIGMAS", ),
            }
            }
    
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "generatenoise"
    CATEGORY = "KJNodes/noise"
    DESCRIPTION = """
Generates noise for injection or to be used as empty latents on samplers with add_noise off.
"""
        
    def generatenoise(self, batch_size, width, height, seed, multiplier, constant_batch_noise, normalize, sigmas=None, model=None):

        generator = torch.manual_seed(seed)
        noise = torch.randn([batch_size, 4, height // 8, width // 8], dtype=torch.float32, layout=torch.strided, generator=generator, device="cpu")
        if sigmas is not None:
            sigma = sigmas[0] - sigmas[-1]
            sigma /= model.model.latent_format.scale_factor
            noise *= sigma

        noise *=multiplier

        if normalize:
            noise = noise / noise.std()
        if constant_batch_noise:
            noise = noise[0].repeat(batch_size, 1, 1, 1)
        return ({"samples":noise}, )

def camera_embeddings(elevation, azimuth):
    elevation = torch.as_tensor([elevation])
    azimuth = torch.as_tensor([azimuth])
    embeddings = torch.stack(
        [
                torch.deg2rad(
                    (90 - elevation) - (90)
                ),  # Zero123 polar is 90-elevation
                torch.sin(torch.deg2rad(azimuth)),
                torch.cos(torch.deg2rad(azimuth)),
                torch.deg2rad(
                    90 - torch.full_like(elevation, 0)
                ),
        ], dim=-1).unsqueeze(1)

    return embeddings

def interpolate_angle(start, end, fraction):
    # Calculate the difference in angles and adjust for wraparound if necessary
    diff = (end - start + 540) % 360 - 180
    # Apply fraction to the difference
    interpolated = start + fraction * diff
    # Normalize the result to be within the range of -180 to 180
    return (interpolated + 180) % 360 - 180


class StableZero123_BatchSchedule:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "clip_vision": ("CLIP_VISION",),
                              "init_image": ("IMAGE",),
                              "vae": ("VAE",),
                              "width": ("INT", {"default": 256, "min": 16, "max": MAX_RESOLUTION, "step": 8}),
                              "height": ("INT", {"default": 256, "min": 16, "max": MAX_RESOLUTION, "step": 8}),
                              "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
                              "interpolation": (["linear", "ease_in", "ease_out", "ease_in_out"],),
                              "azimuth_points_string": ("STRING", {"default": "0:(0.0),\n7:(1.0),\n15:(0.0)\n", "multiline": True}),
                              "elevation_points_string": ("STRING", {"default": "0:(0.0),\n7:(0.0),\n15:(0.0)\n", "multiline": True}),
                             }}
    
    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive", "negative", "latent")
    FUNCTION = "encode"
    CATEGORY = "KJNodes/experimental"

    def encode(self, clip_vision, init_image, vae, width, height, batch_size, azimuth_points_string, elevation_points_string, interpolation):
        output = clip_vision.encode_image(init_image)
        pooled = output.image_embeds.unsqueeze(0)
        pixels = comfy.utils.common_upscale(init_image.movedim(-1,1), width, height, "bilinear", "center").movedim(1,-1)
        encode_pixels = pixels[:,:,:,:3]
        t = vae.encode(encode_pixels)

        def ease_in(t):
            return t * t
        def ease_out(t):
            return 1 - (1 - t) * (1 - t)
        def ease_in_out(t):
            return 3 * t * t - 2 * t * t * t
        
        # Parse the azimuth input string into a list of tuples
        azimuth_points = []
        azimuth_points_string = azimuth_points_string.rstrip(',\n')
        for point_str in azimuth_points_string.split(','):
            frame_str, azimuth_str = point_str.split(':')
            frame = int(frame_str.strip())
            azimuth = float(azimuth_str.strip()[1:-1]) 
            azimuth_points.append((frame, azimuth))
        # Sort the points by frame number
        azimuth_points.sort(key=lambda x: x[0])

        # Parse the elevation input string into a list of tuples
        elevation_points = []
        elevation_points_string = elevation_points_string.rstrip(',\n')
        for point_str in elevation_points_string.split(','):
            frame_str, elevation_str = point_str.split(':')
            frame = int(frame_str.strip())
            elevation_val = float(elevation_str.strip()[1:-1]) 
            elevation_points.append((frame, elevation_val))
        # Sort the points by frame number
        elevation_points.sort(key=lambda x: x[0])

        # Index of the next point to interpolate towards
        next_point = 1
        next_elevation_point = 1

        positive_cond_out = []
        positive_pooled_out = []
        negative_cond_out = []
        negative_pooled_out = []
        
        #azimuth interpolation
        for i in range(batch_size):
            # Find the interpolated azimuth for the current frame
            while next_point < len(azimuth_points) and i >= azimuth_points[next_point][0]:
                next_point += 1
            # If next_point is equal to the length of points, we've gone past the last point
            if next_point == len(azimuth_points):
                next_point -= 1  # Set next_point to the last index of points
            prev_point = max(next_point - 1, 0)  # Ensure prev_point is not less than 0

            # Calculate fraction
            if azimuth_points[next_point][0] != azimuth_points[prev_point][0]:  # Prevent division by zero
                fraction = (i - azimuth_points[prev_point][0]) / (azimuth_points[next_point][0] - azimuth_points[prev_point][0])
                if interpolation == "ease_in":
                    fraction = ease_in(fraction)
                elif interpolation == "ease_out":
                    fraction = ease_out(fraction)
                elif interpolation == "ease_in_out":
                    fraction = ease_in_out(fraction)
                
                # Use the new interpolate_angle function
                interpolated_azimuth = interpolate_angle(azimuth_points[prev_point][1], azimuth_points[next_point][1], fraction)
            else:
                interpolated_azimuth = azimuth_points[prev_point][1]
            # Interpolate the elevation
            next_elevation_point = 1
            while next_elevation_point < len(elevation_points) and i >= elevation_points[next_elevation_point][0]:
                next_elevation_point += 1
            if next_elevation_point == len(elevation_points):
                next_elevation_point -= 1
            prev_elevation_point = max(next_elevation_point - 1, 0)

            if elevation_points[next_elevation_point][0] != elevation_points[prev_elevation_point][0]:
                fraction = (i - elevation_points[prev_elevation_point][0]) / (elevation_points[next_elevation_point][0] - elevation_points[prev_elevation_point][0])
                if interpolation == "ease_in":
                    fraction = ease_in(fraction)
                elif interpolation == "ease_out":
                    fraction = ease_out(fraction)
                elif interpolation == "ease_in_out":
                    fraction = ease_in_out(fraction)
                
                interpolated_elevation = interpolate_angle(elevation_points[prev_elevation_point][1], elevation_points[next_elevation_point][1], fraction)
            else:
                interpolated_elevation = elevation_points[prev_elevation_point][1]

            cam_embeds = camera_embeddings(interpolated_elevation, interpolated_azimuth)
            cond = torch.cat([pooled, cam_embeds.repeat((pooled.shape[0], 1, 1))], dim=-1)

            positive_pooled_out.append(t)
            positive_cond_out.append(cond)
            negative_pooled_out.append(torch.zeros_like(t))
            negative_cond_out.append(torch.zeros_like(pooled))

        # Concatenate the conditions and pooled outputs
        final_positive_cond = torch.cat(positive_cond_out, dim=0)
        final_positive_pooled = torch.cat(positive_pooled_out, dim=0)
        final_negative_cond = torch.cat(negative_cond_out, dim=0)
        final_negative_pooled = torch.cat(negative_pooled_out, dim=0)

        # Structure the final output
        final_positive = [[final_positive_cond, {"concat_latent_image": final_positive_pooled}]]
        final_negative = [[final_negative_cond, {"concat_latent_image": final_negative_pooled}]]

        latent = torch.zeros([batch_size, 4, height // 8, width // 8])
        return (final_positive, final_negative, {"samples": latent})

def linear_interpolate(start, end, fraction):
    return start + (end - start) * fraction

class SV3D_BatchSchedule:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "clip_vision": ("CLIP_VISION",),
                              "init_image": ("IMAGE",),
                              "vae": ("VAE",),
                              "width": ("INT", {"default": 576, "min": 16, "max": MAX_RESOLUTION, "step": 8}),
                              "height": ("INT", {"default": 576, "min": 16, "max": MAX_RESOLUTION, "step": 8}),
                              "batch_size": ("INT", {"default": 21, "min": 1, "max": 4096}),
                              "interpolation": (["linear", "ease_in", "ease_out", "ease_in_out"],),
                              "azimuth_points_string": ("STRING", {"default": "0:(0.0),\n9:(180.0),\n20:(360.0)\n", "multiline": True}),
                              "elevation_points_string": ("STRING", {"default": "0:(0.0),\n9:(0.0),\n20:(0.0)\n", "multiline": True}),
                             }}
    
    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive", "negative", "latent")
    FUNCTION = "encode"
    CATEGORY = "KJNodes/experimental"
    DESCRIPTION = """
Allow scheduling of the azimuth and elevation conditions for SV3D.  
Note that SV3D is still a video model and the schedule needs to always go forward  
https://huggingface.co/stabilityai/sv3d
"""

    def encode(self, clip_vision, init_image, vae, width, height, batch_size, azimuth_points_string, elevation_points_string, interpolation):
        output = clip_vision.encode_image(init_image)
        pooled = output.image_embeds.unsqueeze(0)
        pixels = comfy.utils.common_upscale(init_image.movedim(-1,1), width, height, "bilinear", "center").movedim(1,-1)
        encode_pixels = pixels[:,:,:,:3]
        t = vae.encode(encode_pixels)

        def ease_in(t):
            return t * t
        def ease_out(t):
            return 1 - (1 - t) * (1 - t)
        def ease_in_out(t):
            return 3 * t * t - 2 * t * t * t
        
        # Parse the azimuth input string into a list of tuples
        azimuth_points = []
        azimuth_points_string = azimuth_points_string.rstrip(',\n')
        for point_str in azimuth_points_string.split(','):
            frame_str, azimuth_str = point_str.split(':')
            frame = int(frame_str.strip())
            azimuth = float(azimuth_str.strip()[1:-1]) 
            azimuth_points.append((frame, azimuth))
        # Sort the points by frame number
        azimuth_points.sort(key=lambda x: x[0])

        # Parse the elevation input string into a list of tuples
        elevation_points = []
        elevation_points_string = elevation_points_string.rstrip(',\n')
        for point_str in elevation_points_string.split(','):
            frame_str, elevation_str = point_str.split(':')
            frame = int(frame_str.strip())
            elevation_val = float(elevation_str.strip()[1:-1]) 
            elevation_points.append((frame, elevation_val))
        # Sort the points by frame number
        elevation_points.sort(key=lambda x: x[0])

        # Index of the next point to interpolate towards
        next_point = 1
        next_elevation_point = 1
        elevations = []
        azimuths = []
        # For azimuth interpolation
        for i in range(batch_size):
            # Find the interpolated azimuth for the current frame
            while next_point < len(azimuth_points) and i >= azimuth_points[next_point][0]:
                next_point += 1
            if next_point == len(azimuth_points):
                next_point -= 1
            prev_point = max(next_point - 1, 0)

            if azimuth_points[next_point][0] != azimuth_points[prev_point][0]:
                fraction = (i - azimuth_points[prev_point][0]) / (azimuth_points[next_point][0] - azimuth_points[prev_point][0])
                # Apply the ease function to the fraction
                if interpolation == "ease_in":
                    fraction = ease_in(fraction)
                elif interpolation == "ease_out":
                    fraction = ease_out(fraction)
                elif interpolation == "ease_in_out":
                    fraction = ease_in_out(fraction)
                
                interpolated_azimuth = linear_interpolate(azimuth_points[prev_point][1], azimuth_points[next_point][1], fraction)
            else:
                interpolated_azimuth = azimuth_points[prev_point][1]

            # Interpolate the elevation
            next_elevation_point = 1
            while next_elevation_point < len(elevation_points) and i >= elevation_points[next_elevation_point][0]:
                next_elevation_point += 1
            if next_elevation_point == len(elevation_points):
                next_elevation_point -= 1
            prev_elevation_point = max(next_elevation_point - 1, 0)

            if elevation_points[next_elevation_point][0] != elevation_points[prev_elevation_point][0]:
                fraction = (i - elevation_points[prev_elevation_point][0]) / (elevation_points[next_elevation_point][0] - elevation_points[prev_elevation_point][0])
                # Apply the ease function to the fraction
                if interpolation == "ease_in":
                    fraction = ease_in(fraction)
                elif interpolation == "ease_out":
                    fraction = ease_out(fraction)
                elif interpolation == "ease_in_out":
                    fraction = ease_in_out(fraction)
                
                interpolated_elevation = linear_interpolate(elevation_points[prev_elevation_point][1], elevation_points[next_elevation_point][1], fraction)
            else:
                interpolated_elevation = elevation_points[prev_elevation_point][1]

            azimuths.append(interpolated_azimuth)
            elevations.append(interpolated_elevation)

        print("azimuths", azimuths)
        print("elevations", elevations)

        # Structure the final output
        final_positive = [[pooled, {"concat_latent_image": t, "elevation": elevations, "azimuth": azimuths}]]
        final_negative = [[torch.zeros_like(pooled), {"concat_latent_image": torch.zeros_like(t),"elevation": elevations, "azimuth": azimuths}]]

        latent = torch.zeros([batch_size, 4, height // 8, width // 8])
        return (final_positive, final_negative, {"samples": latent})
    
class ImageBatchRepeatInterleaving:
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "repeat"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Repeats each image in a batch by the specified number of times.  
Example batch of 5 images: 0, 1 ,2, 3, 4  
with repeats 2 becomes batch of 10 images: 0, 0, 1, 1, 2, 2, 3, 3, 4, 4  
"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                 "images": ("IMAGE",),
                 "repeats": ("INT", {"default": 1, "min": 1, "max": 4096}),
        },
    } 
    
    def repeat(self, images, repeats):
       
        repeated_images = torch.repeat_interleave(images, repeats=repeats, dim=0)
        return (repeated_images, )

def parse_coordinates(coordinates_str):
    coordinates = {}
    pattern = r'(\d+):\((\d+),(\d+)\)'
    matches = re.findall(pattern, coordinates_str)
    for match in matches:
        index, x, y = map(int, match)
        coordinates[index] = (x, y)
    return coordinates

def interpolate_coordinates(coordinates_dict, batch_size):
    sorted_coords = sorted(coordinates_dict.items())
    interpolated = {}

    for i, ((index1, (x1, y1)), (index2, (x2, y2))) in enumerate(zip(sorted_coords, sorted_coords[1:])):
        distance = index2 - index1
        x_step = (x2 - x1) / distance
        y_step = (y2 - y1) / distance

        for j in range(distance):
            interpolated_x = round(x1 + j * x_step)
            interpolated_y = round(y1 + j * y_step)
            interpolated[index1 + j] = (interpolated_x, interpolated_y)
    interpolated[sorted_coords[-1][0]] = sorted_coords[-1][1]

    # Ensure we have coordinates for all indices in the batch
    last_index, last_coords = sorted_coords[-1]
    for i in range(last_index + 1, batch_size):
        interpolated[i] = last_coords

    return interpolated

def interpolate_coordinates_with_curves(coordinates_dict, batch_size):
    from scipy.interpolate import CubicSpline
    sorted_coords = sorted(coordinates_dict.items())
    x_coords, y_coords = zip(*[coord for index, coord in sorted_coords])

    # Create the spline curve functions
    indices = np.array([index for index, coord in sorted_coords])
    cs_x = CubicSpline(indices, x_coords)
    cs_y = CubicSpline(indices, y_coords)

    # Generate interpolated coordinates using the spline functions
    interpolated_indices = np.arange(0, batch_size)
    interpolated_x = cs_x(interpolated_indices)
    interpolated_y = cs_y(interpolated_indices)

    # Round the interpolated coordinates and create the dictionary
    interpolated = {i: (round(x), round(y)) for i, (x, y) in enumerate(zip(interpolated_x, interpolated_y))}
    return interpolated

def plot_to_tensor(coordinates_dict, interpolated_dict, height, width, box_size):
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
    import matplotlib.patches as patches

    original_x, original_y = zip(*coordinates_dict.values())
    interpolated_x, interpolated_y = zip(*interpolated_dict.values())

    fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
    ax.scatter(original_x, original_y, color='blue', label='Original Points')
    ax.scatter(interpolated_x, interpolated_y, color='red', alpha=0.5, label='Interpolated Points')
    ax.plot(interpolated_x, interpolated_y, color='grey', linestyle='--', linewidth=0.5)
    # Draw a box at each interpolated coordinate
    for x, y in interpolated_dict.values():
        rect = patches.Rectangle((x - box_size/2, y - box_size/2), box_size, box_size,
                                 linewidth=1, edgecolor='green', facecolor='none')
        ax.add_patch(rect)
    ax.set_title('Interpolated Coordinates')
    ax.set_xlabel('X Coordinate')
    ax.set_ylabel('Y Coordinate')
    ax.legend()
    ax.set_xlim(0, width)  # Set the x-axis to match the input latent width
    ax.set_ylim(height, 0)  # Set the y-axis to match the input latent height, with (0,0) at top-left

    canvas = FigureCanvas(fig)
    canvas.draw()

    width, height = fig.get_size_inches() * fig.get_dpi()
    image_np = np.frombuffer(canvas.tostring_rgb(), dtype='uint8').reshape(int(height), int(width), 3)

    image_tensor = torch.from_numpy(image_np).float() / 255.0
    image_tensor = image_tensor.unsqueeze(0)

    plt.close(fig)

    return image_tensor

class GLIGENTextBoxApplyBatch:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"conditioning_to": ("CONDITIONING", ),
                              "latents": ("LATENT", ),
                              "clip": ("CLIP", ),
                              "gligen_textbox_model": ("GLIGEN", ),
                              "text": ("STRING", {"multiline": True}),
                              "width": ("INT", {"default": 64, "min": 8, "max": MAX_RESOLUTION, "step": 8}),
                              "height": ("INT", {"default": 64, "min": 8, "max": MAX_RESOLUTION, "step": 8}),
                              "coordinates": ("STRING", {"multiline": True}),
                              "interpolation": (
                                [   
                                    'straight',
                                    'CubicSpline',
                                ],
                                {
                                "default": 'CubicSpline'
                                 }),
                             }}
    RETURN_TYPES = ("CONDITIONING", "IMAGE",)
    FUNCTION = "append"
    CATEGORY = "KJNodes/experimental"
    DESCRIPTION = """
Experimental, does not function yet as ComfyUI base changes are needed
"""

    def append(self, latents, conditioning_to, clip, gligen_textbox_model, text, width, height, coordinates, interpolation):

        coordinates_dict = parse_coordinates(coordinates)
        batch_size = sum(tensor.size(0) for tensor in latents.values())
        c = []
        cond, cond_pooled = clip.encode_from_tokens(clip.tokenize(text), return_pooled=True)

        # Interpolate coordinates for the entire batch
        if interpolation == 'CubicSpline':
            interpolated_coords = interpolate_coordinates_with_curves(coordinates_dict, batch_size)
        if interpolation == 'straight':
            interpolated_coords = interpolate_coordinates(coordinates_dict, batch_size)

        plot_image_tensor = plot_to_tensor(coordinates_dict, interpolated_coords, 512, 512, height)
        for t in conditioning_to:
            n = [t[0], t[1].copy()]
            
            position_params_batch = [[] for _ in range(batch_size)]  # Initialize a list of empty lists for each batch item
            
            for i in range(batch_size):
                x_position, y_position = interpolated_coords[i] 
                position_param = (cond_pooled, height // 8, width // 8, y_position // 8, x_position // 8)
                position_params_batch[i].append(position_param)  # Append position_param to the correct sublist
                print("x ",x_position, "y ", y_position)
            prev = []
            if "gligen" in n[1]:
                prev = n[1]['gligen'][2]
            else:
                prev = [[] for _ in range(batch_size)]
            # Concatenate prev and position_params_batch, ensuring both are lists of lists
            # and each sublist corresponds to a batch item
            combined_position_params = [prev_item + batch_item for prev_item, batch_item in zip(prev, position_params_batch)]
            n[1]['gligen'] = ("position", gligen_textbox_model, combined_position_params)
            c.append(n)
        
        return (c, plot_image_tensor,)

class ImageUpscaleWithModelBatched:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "upscale_model": ("UPSCALE_MODEL",),
                              "images": ("IMAGE",),
                              "per_batch": ("INT", {"default": 16, "min": 1, "max": 4096, "step": 1}),
                              }}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Same as ComfyUI native model upscaling node,  
but allows setting sub-batches for reduced VRAM usage.
"""
    def upscale(self, upscale_model, images, per_batch):
        
        device = model_management.get_torch_device()
        upscale_model.to(device)
        in_img = images.movedim(-1,-3).to(device)
        
        steps = in_img.shape[0]
        pbar = comfy.utils.ProgressBar(steps)
        t = []
        
        for start_idx in range(0, in_img.shape[0], per_batch):
            sub_images = upscale_model(in_img[start_idx:start_idx+per_batch])
            t.append(sub_images.cpu())
            # Calculate the number of images processed in this batch
            batch_count = sub_images.shape[0]
            # Update the progress bar by the number of images processed in this batch
            pbar.update(batch_count)
        upscale_model.cpu()
        
        t = torch.cat(t, dim=0).permute(0, 2, 3, 1).cpu()

        return (t,)

class ImageNormalize_Neg1_To_1:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { 
                              "images": ("IMAGE",),
    
                              }}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "normalize"
    CATEGORY = "KJNodes/misc"
    DESCRIPTION = """
Normalize the images to be in the range [-1, 1]  
"""

    def normalize(self,images):
        images = images * 2.0 - 1.0
        return (images,)    

folder_paths.add_model_folder_path("intristic_loras", os.path.join(script_directory, "intristic_loras"))

class Intrinsic_lora_sampling:
    def __init__(self):
        self.loaded_lora = None
        
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "model": ("MODEL",),
                "lora_name": (folder_paths.get_filename_list("intristic_loras"), ),
                "task": (
                [   
                    'depth map',
                    'surface normals',
                    'albedo',
                    'shading',
                ],
                {
                "default": 'depth map'
                    }),
                "text": ("STRING", {"multiline": True, "default": ""}),
                "clip": ("CLIP", ),
                "vae": ("VAE", ),
                "per_batch": ("INT", {"default": 16, "min": 1, "max": 4096, "step": 1}),
        },
            "optional": {
            "image": ("IMAGE",),
            "optional_latent": ("LATENT",),
            },
        }

    RETURN_TYPES = ("IMAGE", "LATENT",)
    FUNCTION = "onestepsample"
    CATEGORY = "KJNodes"
    DESCRIPTION = """
Sampler to use the intrinsic loras:  
https://github.com/duxiaodan/intrinsic-lora  
These LoRAs are tiny and thus included  
with this node pack.
"""

    def onestepsample(self, model, lora_name, clip, vae, text, task, per_batch, image=None, optional_latent=None):
        pbar = comfy.utils.ProgressBar(3)

        if optional_latent is None:
            image_list = []
            for start_idx in range(0, image.shape[0], per_batch):
                sub_pixels = vae.vae_encode_crop_pixels(image[start_idx:start_idx+per_batch])
                image_list.append(vae.encode(sub_pixels[:,:,:,:3]))
            sample = torch.cat(image_list, dim=0)
        else:
            sample = optional_latent["samples"]
        noise = torch.zeros(sample.size(), dtype=sample.dtype, layout=sample.layout, device="cpu")
        prompt = task + "," + text
        positive, = CLIPTextEncode.encode(self, clip, prompt)
        negative = positive #negative shouldn't do anything in this scenario

        pbar.update(1)
     
        #custom model sampling to pass latent through as it is
        class X0_PassThrough(comfy.model_sampling.EPS):
            def calculate_denoised(self, sigma, model_output, model_input):
                return model_output
            def calculate_input(self, sigma, noise):
                return noise
        sampling_base = comfy.model_sampling.ModelSamplingDiscrete
        sampling_type = X0_PassThrough

        class ModelSamplingAdvanced(sampling_base, sampling_type):
            pass
        model_sampling = ModelSamplingAdvanced(model.model.model_config)

        #load lora
        model_clone = model.clone()
        lora_path = folder_paths.get_full_path("intristic_loras", lora_name)        
        lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
        self.loaded_lora = (lora_path, lora)

        model_clone_with_lora = comfy.sd.load_lora_for_models(model_clone, None, lora, 1.0, 0)[0]

        model_clone_with_lora.add_object_patch("model_sampling", model_sampling)

        samples = {"samples": comfy.sample.sample(model_clone_with_lora, noise, 1, 1.0, "euler", "simple", positive, negative, sample,
                                  denoise=1.0, disable_noise=True, start_step=0, last_step=1,
                                  force_full_denoise=True, noise_mask=None, callback=None, disable_pbar=True, seed=None)}
        pbar.update(1)

        decoded = []
        for start_idx in range(0, samples["samples"].shape[0], per_batch):
            decoded.append(vae.decode(samples["samples"][start_idx:start_idx+per_batch]))
        image_out = torch.cat(decoded, dim=0)

        pbar.update(1)

        if task == 'depth map':
            imax = image_out.max()
            imin = image_out.min()
            image_out = (image_out-imin)/(imax-imin)
            image_out = torch.max(image_out, dim=3, keepdim=True)[0].repeat(1, 1, 1, 3)
        elif task == 'surface normals':
            image_out = F.normalize(image_out * 2 - 1, dim=3) / 2 + 0.5
            image_out = 1.0 - image_out
        else:
            image_out = image_out.clamp(-1.,1.)
            
        return (image_out, samples,)

class RemapMaskRange:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mask": ("MASK",),
                "min": ("FLOAT", {"default": 0.0,"min": -10.0, "max": 1.0, "step": 0.01}),
                "max": ("FLOAT", {"default": 1.0,"min": 0.0, "max": 10.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "remap"
    CATEGORY = "KJNodes/masking"
    DESCRIPTION = """
Sets new min and max values for the mask.
"""

    def remap(self, mask, min, max):

         # Find the maximum value in the mask
        mask_max = torch.max(mask)
        
        # If the maximum mask value is zero, avoid division by zero by setting it to 1
        mask_max = mask_max if mask_max > 0 else 1
        
        # Scale the mask values to the new range defined by min and max
        # The highest pixel value in the mask will be scaled to max
        scaled_mask = (mask / mask_max) * (max - min) + min
        
        # Clamp the values to ensure they are within [0.0, 1.0]
        scaled_mask = torch.clamp(scaled_mask, min=0.0, max=1.0)
        
        return (scaled_mask, )

class LoadResAdapterNormalization:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "resadapter_path": (folder_paths.get_filename_list("checkpoints"), )
            } 
        }

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "load_res_adapter"
    CATEGORY = "KJNodes/experimental"

    def load_res_adapter(self, model, resadapter_path):
        print("ResAdapter: Checking ResAdapter path")
        resadapter_full_path = folder_paths.get_full_path("checkpoints", resadapter_path)
        if not os.path.exists(resadapter_full_path):
            raise Exception("Invalid model path")
        else:
            print("ResAdapter: Loading ResAdapter normalization weights")
            prefix_to_remove = 'diffusion_model.'
            model_clone = model.clone()
            norm_state_dict = comfy.utils.load_torch_file(resadapter_full_path)
            new_values = {key[len(prefix_to_remove):]: value for key, value in norm_state_dict.items() if key.startswith(prefix_to_remove)}
            print("ResAdapter: Attempting to add patches with ResAdapter weights")
            try:
                for key in model.model.diffusion_model.state_dict().keys():
                    if key in new_values:
                        original_tensor = model.model.diffusion_model.state_dict()[key]
                        new_tensor = new_values[key].to(model.model.diffusion_model.dtype)
                        if original_tensor.shape == new_tensor.shape:
                            model_clone.add_object_patch(f"diffusion_model.{key}.data", new_tensor)
                        else:
                            print("ResAdapter: No match for key: ",key)
            except:
                raise Exception("Could not patch model, this way of patching was added to ComfyUI on March 3rd 2024, is your ComfyUI up to date?")
            print("ResAdapter: Added resnet normalization patches")
            return (model_clone, )
        
class Superprompt:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "instruction_prompt": ("STRING", {"default": 'Expand the following prompt to add more detail', "multiline": True}),
                "prompt": ("STRING", {"default": '', "multiline": True, "forceInput": True}),
                "max_new_tokens": ("INT", {"default": 128, "min": 1, "max": 4096, "step": 1}),
            } 
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "process"
    CATEGORY = "KJNodes/text"
    DESCRIPTION = """
# SuperPrompt
A T5 model fine-tuned on the SuperPrompt dataset for  
upsampling text prompts to more detailed descriptions.  
Meant to be used as a pre-generation step for text-to-image  
models that benefit from more detailed prompts.  
https://huggingface.co/roborovski/superprompt-v1
"""

    def process(self, instruction_prompt, prompt, max_new_tokens):
        device = model_management.get_torch_device()
        from transformers import T5Tokenizer, T5ForConditionalGeneration

        checkpoint_path = os.path.join(script_directory, "models","superprompt-v1")
        tokenizer = T5Tokenizer.from_pretrained("google/flan-t5-small", legacy=False)

        model = T5ForConditionalGeneration.from_pretrained(checkpoint_path, device_map=device)
        model.to(device)
        input_text = instruction_prompt + ": " + prompt
        print(input_text)
        input_ids = tokenizer(input_text, return_tensors="pt").input_ids.to(device)
        outputs = model.generate(input_ids,  max_new_tokens=max_new_tokens)
        out = (tokenizer.decode(outputs[0]))
        out = out.replace('<pad>', '')
        out = out.replace('</s>', '')
        print(out)
        
        return (out, )

class RemapImageRange:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { 
            "image": ("IMAGE",),
            "min": ("FLOAT", {"default": 0.0,"min": -10.0, "max": 1.0, "step": 0.01}),
            "max": ("FLOAT", {"default": 1.0,"min": 0.0, "max": 10.0, "step": 0.01}),
            "clamp": ("BOOLEAN", {"default": True}),
            },
            }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "remap"
    CATEGORY = "KJNodes/image"
    DESCRIPTION = """
Remaps the image values to the specified range. 
"""
        
    def remap(self, image, min, max, clamp):
        if image.dtype == torch.float16:
            image = image.to(torch.float32)
        image = min + image * (max - min)
        if clamp:
            image = torch.clamp(image, min=0.0, max=1.0)
        return (image, )
    
class CameraPoseVisualizer:
                
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "pose_file_path": ("STRING", {"default": '', "multiline": False}),
            "base_xval": ("FLOAT", {"default": 0.2,"min": 0, "max": 100, "step": 0.01}),
            "zval": ("FLOAT", {"default": 0.3,"min": 0, "max": 100, "step": 0.01}),
            "scale": ("FLOAT", {"default": 1.0,"min": 0.01, "max": 10.0, "step": 0.01}),
            "use_exact_fx": ("BOOLEAN", {"default": False}),
            "relative_c2w": ("BOOLEAN", {"default": True}),
            "use_viewer": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "cameractrl_poses": ("CAMERACTRL_POSES", {"default": None}),
            }
            }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "plot"
    CATEGORY = "KJNodes/misc"
    DESCRIPTION = """
Visualizes the camera poses, from Animatediff-Evolved CameraCtrl Pose  
or a .txt file with RealEstate camera intrinsics and coordinates, in a 3D plot. 
"""
        
    def plot(self, pose_file_path, scale, base_xval, zval, use_exact_fx, relative_c2w, use_viewer, cameractrl_poses=None):
        import matplotlib as mpl
        import matplotlib.pyplot as plt
        import io
        from torchvision.transforms import ToTensor

        x_min = -2.0 * scale
        x_max = 2.0 * scale
        y_min = -2.0 * scale
        y_max = 2.0 * scale
        z_min = -2.0 * scale
        z_max = 2.0 * scale
        plt.rcParams['text.color'] = '#999999'
        self.fig = plt.figure(figsize=(18, 7))
        self.fig.patch.set_facecolor('#353535')
        self.ax = self.fig.add_subplot(projection='3d')
        self.ax.set_facecolor('#353535') # Set the background color here
        self.ax.grid(color='#999999', linestyle='-', linewidth=0.5)
        self.plotly_data = None  # plotly data traces
        self.ax.set_aspect("auto")
        self.ax.set_xlim(x_min, x_max)
        self.ax.set_ylim(y_min, y_max)
        self.ax.set_zlim(z_min, z_max)
        self.ax.set_xlabel('x', color='#999999')
        self.ax.set_ylabel('y', color='#999999')
        self.ax.set_zlabel('z', color='#999999')
        for text in self.ax.get_xticklabels() + self.ax.get_yticklabels() + self.ax.get_zticklabels():
            text.set_color('#999999')
        print('initialize camera pose visualizer')

        if pose_file_path != "":
            with open(pose_file_path, 'r') as f:
                poses = f.readlines()
                w2cs = [np.asarray([float(p) for p in pose.strip().split(' ')[7:]]).reshape(3, 4) for pose in poses[1:]]
                fxs = [float(pose.strip().split(' ')[1]) for pose in poses[1:]]
                print(poses)
        elif cameractrl_poses is not None:
            poses = cameractrl_poses
            w2cs = [np.array(pose[7:]).reshape(3, 4) for pose in cameractrl_poses]
            fxs = [pose[1] for pose in cameractrl_poses]
        else:
            raise ValueError("Please provide either pose_file_path or cameractrl_poses")

        total_frames = len(w2cs)
        transform_matrix = np.asarray([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]]).reshape(4, 4)
        last_row = np.zeros((1, 4))
        last_row[0, -1] = 1.0

        w2cs = [np.concatenate((w2c, last_row), axis=0) for w2c in w2cs]
        c2ws = self.get_c2w(w2cs, transform_matrix, relative_c2w)

        for frame_idx, c2w in enumerate(c2ws):
            self.extrinsic2pyramid(c2w, frame_idx / total_frames, hw_ratio=1/1, base_xval=base_xval,
                                    zval=(fxs[frame_idx] if use_exact_fx else zval))

        # Create the colorbar
        cmap = mpl.cm.rainbow
        norm = mpl.colors.Normalize(vmin=0, vmax=total_frames)
        colorbar = self.fig.colorbar(mpl.cm.ScalarMappable(norm=norm, cmap=cmap), ax=self.ax, orientation='vertical')

        # Change the colorbar label
        colorbar.set_label('Frame', color='#999999') # Change the label and its color

        # Change the tick colors
        colorbar.ax.yaxis.set_tick_params(colors='#999999') # Change the tick color

        # Change the tick frequency
        # Assuming you want to set the ticks at every 10th frame
        ticks = np.arange(0, total_frames, 10)
        colorbar.ax.yaxis.set_ticks(ticks)
        
        plt.title('')
        plt.draw()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0)
        buf.seek(0)
        img = Image.open(buf)
        tensor_img = ToTensor()(img)
        buf.close()
        tensor_img = tensor_img.permute(1, 2, 0).unsqueeze(0)
        if use_viewer:
            time.sleep(1)
            plt.show()
        return (tensor_img,)

    def extrinsic2pyramid(self, extrinsic, color_map='red', hw_ratio=1/1, base_xval=1, zval=3):
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        vertex_std = np.array([[0, 0, 0, 1],
                            [base_xval, -base_xval * hw_ratio, zval, 1],
                            [base_xval, base_xval * hw_ratio, zval, 1],
                            [-base_xval, base_xval * hw_ratio, zval, 1],
                            [-base_xval, -base_xval * hw_ratio, zval, 1]])
        vertex_transformed = vertex_std @ extrinsic.T
        meshes = [[vertex_transformed[0, :-1], vertex_transformed[1][:-1], vertex_transformed[2, :-1]],
                            [vertex_transformed[0, :-1], vertex_transformed[2, :-1], vertex_transformed[3, :-1]],
                            [vertex_transformed[0, :-1], vertex_transformed[3, :-1], vertex_transformed[4, :-1]],
                            [vertex_transformed[0, :-1], vertex_transformed[4, :-1], vertex_transformed[1, :-1]],
                            [vertex_transformed[1, :-1], vertex_transformed[2, :-1], vertex_transformed[3, :-1], vertex_transformed[4, :-1]]]

        color = color_map if isinstance(color_map, str) else plt.cm.rainbow(color_map)

        self.ax.add_collection3d(
            Poly3DCollection(meshes, facecolors=color, linewidths=0.3, edgecolors=color, alpha=0.25))

    def customize_legend(self, list_label):
        from matplotlib.patches import Patch
        list_handle = []
        for idx, label in enumerate(list_label):
            color = plt.cm.rainbow(idx / len(list_label))
            patch = Patch(color=color, label=label)
            list_handle.append(patch)
        plt.legend(loc='right', bbox_to_anchor=(1.8, 0.5), handles=list_handle)

    def get_c2w(self, w2cs, transform_matrix, relative_c2w):
        if relative_c2w:
            target_cam_c2w = np.array([
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
                [0, 0, 0, 1]
            ])
            abs2rel = target_cam_c2w @ w2cs[0]
            ret_poses = [target_cam_c2w, ] + [abs2rel @ np.linalg.inv(w2c) for w2c in w2cs[1:]]
        else:
            ret_poses = [np.linalg.inv(w2c) for w2c in w2cs]
        ret_poses = [transform_matrix @ x for x in ret_poses]
        return np.array(ret_poses, dtype=np.float32)
    
class ImagePadForOutpaintMasked:

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "left": ("INT", {"default": 0, "min": 0, "max": MAX_RESOLUTION, "step": 8}),
                "top": ("INT", {"default": 0, "min": 0, "max": MAX_RESOLUTION, "step": 8}),
                "right": ("INT", {"default": 0, "min": 0, "max": MAX_RESOLUTION, "step": 8}),
                "bottom": ("INT", {"default": 0, "min": 0, "max": MAX_RESOLUTION, "step": 8}),
                "feathering": ("INT", {"default": 40, "min": 0, "max": MAX_RESOLUTION, "step": 1}),
            },
            "optional": {
                "mask": ("MASK",),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    FUNCTION = "expand_image"

    CATEGORY = "image"

    def expand_image(self, image, left, top, right, bottom, feathering, mask=None):
        B, H, W, C = image.size()

        new_image = torch.ones(
            (B, H + top + bottom, W + left + right, C),
            dtype=torch.float32,
        ) * 0.5

        new_image[:, top:top + H, left:left + W, :] = image

        if mask is None:
            new_mask = torch.ones(
                (H + top + bottom, W + left + right),
                dtype=torch.float32,
            )

            t = torch.zeros(
            (H, W),
            dtype=torch.float32
            )
        else:
            # If a mask is provided, pad it to fit the new image size
            mask = F.pad(mask, (left, right, top, bottom), mode='constant', value=0)
            mask = 1 - mask
            t = torch.zeros_like(mask)

        
        
        if feathering > 0 and feathering * 2 < H and feathering * 2 < W:

            for i in range(H):
                for j in range(W):
                    dt = i if top != 0 else H
                    db = H - i if bottom != 0 else H

                    dl = j if left != 0 else W
                    dr = W - j if right != 0 else W

                    d = min(dt, db, dl, dr)

                    if d >= feathering:
                        continue

                    v = (feathering - d) / feathering

                    if mask is None:
                        t[i, j] = v * v
                    else:
                        t[:, top + i, left + j] = v * v
        
        if mask is None:
            mask = new_mask.squeeze(0)
            mask[top:top + H, left:left + W] = t
            mask = mask.unsqueeze(0)

        return (new_image, mask,)
    
class ImageAndMaskPreview(SaveImage):
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mask_opacity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "mask_color": ("STRING", {"default": "255, 255, 255"}),
                "pass_through": ("BOOLEAN", {"default": False}),
             },
            "optional": {
                "image": ("IMAGE",),
                "mask": ("MASK",),                
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("composite",)
    FUNCTION = "execute"
    CATEGORY = "KJNodes"
    DESCRIPTION = """
Preview an image or a mask, when both inputs are used  
composites the mask on top of the image.
with pass_through on the preview is disabled and the  
composite is returned from the composite slot instead,  
this allows for the preview to be passed for video combine  
nodes for example.
"""

    def execute(self, mask_opacity, mask_color, pass_through, filename_prefix="ComfyUI", image=None, mask=None, prompt=None, extra_pnginfo=None):
        if mask is not None and image is None:
            preview = mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).movedim(1, -1).expand(-1, -1, -1, 3)
        elif mask is None and image is not None:
            preview = image
        elif mask is not None and image is not None:
            mask_adjusted = mask * mask_opacity
            mask_image = mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).movedim(1, -1).expand(-1, -1, -1, 3).clone()

            color_list = list(map(int, mask_color.split(', ')))
            print(color_list[0])
            mask_image[:, :, :, 0] = color_list[0] // 255 # Red channel
            mask_image[:, :, :, 1] = color_list[1] // 255 # Green channel
            mask_image[:, :, :, 2] = color_list[2] // 255 # Blue channel
            
            preview, = ImageCompositeMasked.composite(self, image, mask_image, 0, 0, True, mask_adjusted)
        if pass_through:
            return (preview, )
        return(self.save_images(preview, filename_prefix, prompt, extra_pnginfo))
        
    


class StabilityAPI_SD3:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "n_prompt": ("STRING", {"multiline": True}),
                "seed": ("INT", {"default": 123,"min": 0, "max": 4294967294, "step": 1}),
                "model": (
                [   
                    'sd3',
                    'sd3-turbo',
                ],
                {
                "default": 'sd3'
                 }),
                 "aspect_ratio": (
                [   
                    '1:1',
                    '16:9',
                    '21:9',
                    '2:3',
                    '3:2',
                    '4:5',
                    '5:4',
                    '9:16',
                    '9:21',
                ],
                {
                "default": '1:1'
                }),
                "output_format": (
                [   
                    'png',
                    'jpeg',
                ],
                {
                "default": 'jpeg'
                 }),                 
            },
            "optional": {
                "api_key": ("STRING", {"multiline": True}),
                "image": ("IMAGE",),
                "img2img_strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
                "disable_metadata": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "apicall"

    CATEGORY = "KJNodes/experimental"
    DESCRIPTION = """
## Calls StabilityAI API
   
Although you may have multiple keys in your account,  
you should use the same key for all requests to this API.  

Get your API key here: https://platform.stability.ai/account/keys  
Recommended to set the key in the config.json -file under this  
node packs folder.  
# WARNING:  
Otherwise the API key may get saved in the image metadata even  
with "disable_metadata" on if the workflow includes save nodes  
separate from this node.  
   
sd3 requires 6.5 credits per generation  
sd3-turbo requires 4 credits per generation  

If no image is provided, mode is set to text-to-image  

"""

    def apicall(self, prompt, n_prompt, model, seed, aspect_ratio, output_format, 
                img2img_strength=0.5, image=None, disable_metadata=True, api_key=""):
        from comfy.cli_args import args
        if disable_metadata:
            args.disable_metadata = True
        else:
            args.disable_metadata = False
        
        import requests
        from io import BytesIO
        from torchvision import transforms
        
        data = {
                "mode": "text-to-image",
                "prompt": prompt,
                "model": model,
                "seed": seed,
                "output_format": output_format
                }
        
        if image is not None:
            image = image.permute(0, 3, 1, 2).squeeze(0)
            to_pil = transforms.ToPILImage()
            pil_image = to_pil(image)
            # Save the PIL Image to a BytesIO object
            buffer = BytesIO()
            pil_image.save(buffer, format='PNG')
            buffer.seek(0)
            files = {"image": ("image.png", buffer, "image/png")}
           
            data["mode"] = "image-to-image"
            data["image"] = pil_image
            data["strength"] = img2img_strength
        else:
            data["aspect_ratio"] = aspect_ratio,
            files = {"none": ''}
        
        if model != "sd3-turbo":
            data["negative_prompt"] = n_prompt

        headers={
                "accept": "image/*"
            }
        
        if api_key != "":
            headers["authorization"] = api_key
        else:
            config_file_path = os.path.join(script_directory,"config.json")
            with open(config_file_path, 'r') as file:
                config = json.load(file)
            api_key_from_config = config.get("sai_api_key")
            headers["authorization"] = api_key_from_config            
        
        response = requests.post(
            f"https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers=headers,
            files = files,
            data = data,
        )

        if response.status_code == 200:
            # Convert the response content to a PIL Image
            image = Image.open(BytesIO(response.content))
            # Convert the PIL Image to a PyTorch tensor
            transform = transforms.ToTensor()
            tensor_image = transform(image)
            tensor_image = tensor_image.unsqueeze(0)
            tensor_image = tensor_image.permute(0, 2, 3, 1).cpu().float()
            return (tensor_image,)
        else:
            try:
                # Attempt to parse the response as JSON
                error_data = response.json()
                raise Exception(f"Server error: {error_data}")
            except json.JSONDecodeError:
                # If the response is not valid JSON, raise a different exception
                raise Exception(f"Server error: {response.text}")